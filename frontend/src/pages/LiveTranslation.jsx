import { useEffect, useRef, useState } from 'react';

// The microphone is streamed continuously as 16 kHz PCM (see
// public/pcm-recorder-worklet.js). The server detects when the caller speaks
// and pauses, sends partial text while they talk and a final, translated
// sentence after each pause.

const STATUS = {
  idle: { label: 'Not started', className: 'bg-secondary' },
  connecting: { label: 'Connecting…', className: 'bg-secondary' },
  listening: { label: 'Listening — speak now', className: 'bg-success' },
  speaking: { label: 'Hearing speech…', className: 'bg-danger' },
  processing: { label: 'Translating…', className: 'bg-warning text-dark' },
  reconnecting: { label: 'Reconnecting…', className: 'bg-secondary' },
};

const websocketBase = () => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return process.env.REACT_APP_WS_URL || (
    process.env.NODE_ENV === 'production'
      ? `${protocol}//${window.location.host}`
      : `${protocol}//${window.location.hostname}:8000`
  );
};

const formatTime = (seconds) =>
  `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;

function LiveTranslation() {
  const [sourceLanguage, setSourceLanguage] = useState('Auto');
  const [isLive, setIsLive] = useState(false);
  const [status, setStatus] = useState('idle');
  const [segments, setSegments] = useState([]);
  const [partial, setPartial] = useState(null);
  const [emergencyType, setEmergencyType] = useState('Unknown');
  const [detectedLanguage, setDetectedLanguage] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState('');

  const socketRef = useRef(null);
  const audioContextRef = useRef(null);
  const streamRef = useRef(null);
  const workletRef = useRef(null);
  const timerRef = useRef(null);
  const pingRef = useRef(null);
  const liveRef = useRef(false);
  const reconnectsRef = useRef(0);
  const transcriptEndRef = useRef(null);

  useEffect(() => () => stopEverything(), []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [segments, partial]);

  const handleMessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (message.type === 'status') {
      setStatus(message.state);
    } else if (message.type === 'partial') {
      setPartial((previous) => ({
        id: message.id,
        text: message.text,
        // Keep the previous quick translation until a newer one arrives.
        translation: message.translation ?? (previous?.id === message.id ? previous.translation : ''),
      }));
      if (message.detected_language) setDetectedLanguage(message.detected_language);
    } else if (message.type === 'final') {
      setPartial((previous) => (previous?.id === message.id ? null : previous));
      if (message.text) {
        setSegments((previous) => [...previous, {
          id: `${Date.now()}-${message.id}`,
          text: message.text,
          translation: message.translation,
        }]);
        setDetectedLanguage(message.detected_language || '');
        setEmergencyType(message.emergency_type || 'Unknown');
      }
    } else if (message.type === 'error') {
      setError(message.message);
    }
  };

  const connect = () => {
    const query = new URLSearchParams({ language: sourceLanguage });
    const socket = new WebSocket(`${websocketBase()}/ws/live-transcription?${query}`);
    socket.binaryType = 'arraybuffer';
    socketRef.current = socket;
    setStatus(reconnectsRef.current ? 'reconnecting' : 'connecting');

    socket.onopen = () => {
      reconnectsRef.current = 0;
      setError('');
    };
    socket.onmessage = handleMessage;
    socket.onclose = () => {
      if (socketRef.current !== socket || !liveRef.current) return;
      // Unexpected close while live: reconnect, keeping the transcript on screen.
      if (reconnectsRef.current < 5) {
        reconnectsRef.current += 1;
        setStatus('reconnecting');
        setTimeout(() => liveRef.current && connect(), 1000 * reconnectsRef.current);
      } else {
        setError('Lost the connection to the server. Please press Start again.');
        stopEverything();
      }
    };
  };

  const startLive = async () => {
    setError('');
    setSegments([]);
    setPartial(null);
    setEmergencyType('Unknown');
    setDetectedLanguage('');
    setElapsed(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      const audioContext = new AudioContext();
      audioContextRef.current = audioContext;
      await audioContext.audioWorklet.addModule(`${process.env.PUBLIC_URL || ''}/pcm-recorder-worklet.js`);
      const source = audioContext.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(audioContext, 'pcm-recorder');
      worklet.port.onmessage = ({ data }) => {
        const socket = socketRef.current;
        if (socket?.readyState === WebSocket.OPEN) socket.send(data);
      };
      source.connect(worklet);
      workletRef.current = worklet;

      liveRef.current = true;
      reconnectsRef.current = 0;
      setIsLive(true);
      connect();
      timerRef.current = setInterval(() => setElapsed((value) => value + 1), 1000);
      pingRef.current = setInterval(() => {
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(JSON.stringify({ type: 'ping' }));
        }
      }, 20000);
    } catch (err) {
      setError('Unable to start live translation. Please allow microphone access and try again.');
      stopEverything();
    }
  };

  const stopAudio = () => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    audioContextRef.current?.close().catch(() => {});
    audioContextRef.current = null;
  };

  function stopEverything() {
    liveRef.current = false;
    clearInterval(timerRef.current);
    clearInterval(pingRef.current);
    stopAudio();
    const socket = socketRef.current;
    socketRef.current = null;
    socket?.close();
    setIsLive(false);
    setStatus('idle');
  }

  const stopLive = () => {
    liveRef.current = false;
    clearInterval(timerRef.current);
    clearInterval(pingRef.current);
    stopAudio();
    setIsLive(false);
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) {
      stopEverything();
      return;
    }
    // Ask the server to finish the last sentence, then close once it is done.
    setStatus('processing');
    socket.send(JSON.stringify({ type: 'stop' }));
    const finish = () => {
      if (socketRef.current === socket) socketRef.current = null;
      socket.close();
      setStatus('idle');
    };
    const timeout = setTimeout(finish, 60000);
    socket.onmessage = (event) => {
      handleMessage(event);
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'status' && message.state === 'listening') {
          clearTimeout(timeout);
          finish();
        }
      } catch { /* ignore */ }
    };
  };

  const statusInfo = STATUS[status] || STATUS.idle;
  const hasText = segments.length > 0 || partial;

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

        <div className='d-flex flex-wrap align-items-center gap-2 mb-3'>
          <button className='btn btn-primary' onClick={startLive} disabled={isLive}>
            {isLive ? 'Live translating…' : 'Start live translation'}
          </button>
          <button className='btn btn-danger' onClick={stopLive} disabled={!isLive}>
            Stop
          </button>
          <span className={`badge rounded-pill ms-md-2 px-3 py-2 ${statusInfo.className}`}>{statusInfo.label}</span>
          {isLive && <span className='text-muted small'>{formatTime(elapsed)}</span>}
        </div>

        {(detectedLanguage || emergencyType !== 'Unknown') && (
          <div className='d-flex flex-wrap gap-2 mb-3'>
            {detectedLanguage && <span className='badge bg-info text-dark px-3 py-2'>Language: {detectedLanguage}</span>}
            {emergencyType !== 'Unknown' && (
              <span className='badge bg-danger px-3 py-2'>Emergency: {emergencyType}</span>
            )}
          </div>
        )}

        {hasText ? (
          <div className='border rounded p-3 bg-light' style={{ maxHeight: '60vh', overflowY: 'auto' }}>
            {segments.map((segment) => (
              <div key={segment.id} className='mb-3 pb-2 border-bottom'>
                <div dir='auto' className='fs-5'>{segment.text}</div>
                <div className='text-primary'>{segment.translation}</div>
              </div>
            ))}
            {partial && (
              <div className='mb-2'>
                <div dir='auto' className='fs-5 text-secondary fst-italic'>{partial.text}</div>
                {partial.translation && <div className='text-primary opacity-75 fst-italic'>{partial.translation}</div>}
              </div>
            )}
            <div ref={transcriptEndRef} />
          </div>
        ) : (
          <p className='text-muted'>
            {isLive
              ? 'Speak now. Your words appear here as you talk, and the translation follows after each pause.'
              : 'Press Start and speak. Choosing the caller\'s language gives the best results, especially for Pashto.'}
          </p>
        )}
      </div>
    </div>
  );
}

export default LiveTranslation;
