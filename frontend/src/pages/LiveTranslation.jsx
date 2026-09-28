import { useEffect, useRef, useState } from 'react';

// Each clip is a complete WebM file the backend can decode on its own.
const SEGMENT_MS = 5000;

const combineResults = (previous, current) => {
  if (!previous) return current;
  const join = (a, b) => [a, b].filter(Boolean).join(' ').trim();
  return {
    original_text: join(previous.original_text, current.original_text),
    detected_language: current.detected_language || previous.detected_language,
    translated_text: join(previous.translated_text, current.translated_text),
    emergency_type: current.emergency_type !== 'Unknown' ? current.emergency_type : previous.emergency_type,
  };
};

function LiveTranslation() {
  const [liveResult, setLiveResult] = useState(null);
  const [isLive, setIsLive] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [error, setError] = useState('');
  const [sourceLanguage, setSourceLanguage] = useState('Auto');

  const mediaRecorderRef = useRef(null);
  const socketRef = useRef(null);
  const timerRef = useRef(null);
  const segmentTimerRef = useRef(null);
  const streamRef = useRef(null);
  const manualStopRef = useRef(false);
  const reconnectAttemptsRef = useRef(0);
  const maxReconnectAttemptsRef = useRef(3);
  const pingIntervalRef = useRef(null);
  // Bumped on every (re)connection so recorders from an old connection stop.
  const connectionIdRef = useRef(0);
  // The backend keeps the transcript per connection; keep what earlier
  // connections produced so a reconnect doesn't wipe the screen.
  const earlierResultRef = useRef(null);
  const currentResultRef = useRef(null);

  const createMediaRecorder = (stream) => {
    const connectionId = connectionIdRef.current;
    const mediaRecorder = new MediaRecorder(stream, {
      mimeType: 'audio/webm;codecs=opus'
    });

    mediaRecorder.ondataavailable = (event) => {
      console.log('Data available, size:', event.data.size);
      if (!event.data || event.data.size < 1000) {
        console.log('Skipping small audio chunk');
        return;
      }

      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        console.log('Sending audio segment to server, size:', event.data.size);
        socket.send(event.data);
      } else {
        console.warn('Socket not open, audio segment not sent', socket?.readyState);
      }
    };

    mediaRecorder.onerror = (event) => {
      console.error('MediaRecorder error:', event.error);
      setError('MediaRecorder error: ' + event.error.message);
    };

    mediaRecorder.onstop = () => {
      if (segmentTimerRef.current) {
        clearTimeout(segmentTimerRef.current);
        segmentTimerRef.current = null;
      }
      if (!manualStopRef.current && streamRef.current === stream && connectionIdRef.current === connectionId) {
        const nextRecorder = createMediaRecorder(stream);
        mediaRecorderRef.current = nextRecorder;
        startRecorderSegment(nextRecorder);
      }
    };

    return mediaRecorder;
  };

  const startRecorderSegment = (mediaRecorder) => {
    mediaRecorder.start();
    segmentTimerRef.current = window.setTimeout(() => {
      if (!manualStopRef.current && mediaRecorderRef.current === mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
      }
    }, SEGMENT_MS);
  };

  useEffect(() => {
    return () => {
      manualStopRef.current = true;
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
      if (pingIntervalRef.current) {
        clearInterval(pingIntervalRef.current);
      }
      if (segmentTimerRef.current) {
        clearTimeout(segmentTimerRef.current);
      }
      if (mediaRecorderRef.current?.state === 'recording') {
        mediaRecorderRef.current.stop();
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
      if (socketRef.current) {
        socketRef.current.close();
      }
    };
  }, []);

  const connectWebSocket = (stream, mediaRecorder) => {
    try {
      const websocketProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const websocketHost = process.env.REACT_APP_WS_URL || (
        process.env.NODE_ENV === 'production'
          ? `${websocketProtocol}//${window.location.host}`
          : `${websocketProtocol}//${window.location.hostname}:8000`
      );
      const query = new URLSearchParams({ language: sourceLanguage });
      const socket = new WebSocket(`${websocketHost}/ws/live-transcription?${query}`);
      socketRef.current = socket;

      socket.onopen = () => {
        console.log('WebSocket connected');
        reconnectAttemptsRef.current = 0;
        manualStopRef.current = false;
        setIsLive(true);
        setError('');
        if (timerRef.current) {
          clearInterval(timerRef.current);
        }
        timerRef.current = window.setInterval(() => {
          setElapsedSeconds((prev) => prev + 1);
        }, 1000);

        // Start MediaRecorder only after WebSocket is connected
        try {
          startRecorderSegment(mediaRecorder);
          console.log('MediaRecorder started');
        } catch (recorderError) {
          console.error('Failed to start MediaRecorder:', recorderError);
          setError('Failed to start audio recording: ' + recorderError.message);
        }

        // Send a ping every 30 seconds to keep connection alive
        if (pingIntervalRef.current) {
          clearInterval(pingIntervalRef.current);
        }
        pingIntervalRef.current = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30000);
      };

      socket.onmessage = (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch (parseError) {
          console.warn('Ignoring non-JSON WebSocket message:', event.data);
          return;
        }
        if (data.type === 'pong' || !data.original_text?.trim()) {
          return;
        }
        // The backend sends the whole call so far, translated with full
        // sentence context, so replace rather than append.
        currentResultRef.current = data;
        setLiveResult(combineResults(earlierResultRef.current, data));
      };

      socket.onerror = (error) => {
        console.log('WebSocket error:', error);
        setError('Live translation socket error. Attempting to reconnect...');
      };

      socket.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);
        if (!manualStopRef.current) {
          // Retire this connection's recorder before a new one is created.
          connectionIdRef.current += 1;
          if (segmentTimerRef.current) {
            clearTimeout(segmentTimerRef.current);
            segmentTimerRef.current = null;
          }
          if (mediaRecorderRef.current?.state === 'recording') {
            mediaRecorderRef.current.stop();
          }
          if (currentResultRef.current) {
            earlierResultRef.current = combineResults(earlierResultRef.current, currentResultRef.current);
            currentResultRef.current = null;
          }
          if (reconnectAttemptsRef.current < maxReconnectAttemptsRef.current) {
            reconnectAttemptsRef.current += 1;
            setError(`Connection lost. Reconnecting... (Attempt ${reconnectAttemptsRef.current}/${maxReconnectAttemptsRef.current})`);
            setTimeout(() => {
              if (streamRef.current && !manualStopRef.current) {
                // Create new MediaRecorder for reconnection
                const mediaRecorder = createMediaRecorder(streamRef.current);
                mediaRecorderRef.current = mediaRecorder;

                connectWebSocket(streamRef.current, mediaRecorder);
              }
            }, 2000 * reconnectAttemptsRef.current);
          } else {
            setError('Live translation ended unexpectedly. Maximum reconnection attempts reached. Please restart.');
            setIsLive(false);
          }
        }
        if (timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      };

      return socket;
    } catch (err) {
      console.error('Failed to create WebSocket:', err);
      setError('Failed to connect to live translation server. Please try again.');
      return null;
    }
  };

  const startLive = async () => {
    setError('');
    setLiveResult(null);
    setElapsedSeconds(0);
    reconnectAttemptsRef.current = 0;
    manualStopRef.current = false;
    connectionIdRef.current += 1;
    earlierResultRef.current = null;
    currentResultRef.current = null;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mediaRecorder = createMediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      console.log('MediaRecorder created with mimeType:', mediaRecorder.mimeType);

      const socket = connectWebSocket(stream, mediaRecorder);
      if (!socket) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      // MediaRecorder will be started in socket.onopen

    } catch (err) {
      setError('Unable to start live translation. Please allow microphone access and try again.');
    }
  };

  const stopLive = () => {
    manualStopRef.current = true;
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (segmentTimerRef.current) {
      clearTimeout(segmentTimerRef.current);
      segmentTimerRef.current = null;
    }
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    if (socketRef.current) {
      // Give the backend a moment to return the last clip's result.
      const socket = socketRef.current;
      window.setTimeout(() => socket.close(), 1500);
      socketRef.current = null;
    }
    setIsLive(false);
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    setError('');
  };

  return (
    <div className='container mt-5'>
      <div className='card shadow-lg border-0 p-4'>
        <h2 className='fw-bold mb-4'>Live Translation</h2>

        {error && <div className='alert alert-danger'>{error}</div>}

        <label className='form-label' htmlFor='live-source-language'>Source language</label>
        <select
          id='live-source-language'
          className='form-select mb-3'
          value={sourceLanguage}
          onChange={(event) => setSourceLanguage(event.target.value)}
          disabled={isLive}
        >
          <option value='Auto'>Detect automatically</option>
          <option value='Urdu'>Urdu</option>
          <option value='Pashto'>Pashto</option>
          <option value='Punjabi'>Punjabi</option>
          <option value='English'>English</option>
        </select>

        <div className='mb-3'>
          <button
            className='btn btn-primary me-2'
            onClick={startLive}
            disabled={isLive}
          >
            {isLive ? 'Live Translating...' : 'Translate Live'}
          </button>

          <button
            className='btn btn-danger'
            onClick={stopLive}
            disabled={!isLive}
          >
            Stop Live Translate
          </button>
        </div>

        {isLive && (
          <div className='alert alert-info'>
            <strong>Live translation active</strong> — elapsed time: {Math.floor(elapsedSeconds / 60)
              .toString()
              .padStart(2, '0')}:{(elapsedSeconds % 60).toString().padStart(2, '0')}
          </div>
        )}

        {liveResult ? (
          <div className='card mt-4 shadow-lg border-0'>
            <div className='card-body'>
              <h4 className='fw-bold mb-4'>Real-time Translation Result</h4>
              {liveResult.original_text && (
                <div className='mb-3'>
                  <strong>Original Text:</strong>
                  <pre className='mt-2 p-2 bg-light border rounded' style={{ whiteSpace: 'pre-wrap', wordWrap: 'break-word' }}>
                    {liveResult.original_text}
                  </pre>
                </div>
              )}
              {liveResult.detected_language && (
                <p><strong>Detected Language:</strong> {liveResult.detected_language}</p>
              )}
              {liveResult.translated_text && (
                <div className='mb-3'>
                  <strong>Translated Text:</strong>
                  <pre className='mt-2 p-2 bg-light border rounded' style={{ whiteSpace: 'pre-wrap', wordWrap: 'break-word' }}>
                    {liveResult.translated_text}
                  </pre>
                </div>
              )}
              {liveResult.emergency_type && (
                <p><strong>Emergency Type:</strong> {liveResult.emergency_type}</p>
              )}
            </div>
          </div>
        ) : (
          !error && (
            <div className='mt-4'>
              <p className='text-muted'>Real-time translation results will appear here once live translation starts.</p>
            </div>
          )
        )}
      </div>
    </div>
  );
}

export default LiveTranslation;