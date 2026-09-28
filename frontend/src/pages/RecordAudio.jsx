import { useEffect, useRef, useState } from 'react';
import API from '../services/api';
import ResultCard from '../components/ResultCard';

const formatTime = (seconds) => {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${minutes}:${secs}`;
};

const RecordAudio = () => {
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const timerRef = useRef(null);

  const [isRecording, setIsRecording] = useState(false);
  const [recordTime, setRecordTime] = useState(0);
  const [audioBlob, setAudioBlob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [processTime, setProcessTime] = useState(0);
  const [sourceLanguage, setSourceLanguage] = useState('Auto');

  const processTimerRef = useRef(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
      if (processTimerRef.current) {
        clearInterval(processTimerRef.current);
      }
      if (mediaRecorderRef.current?.stream) {
        mediaRecorderRef.current.stream.getTracks().forEach((track) => track.stop());
      }
    };
  }, []);

  const startRecording = async () => {
    try {
      if (isRecording) return;

      setError('');
      setResult(null);
      setAudioBlob(null);
      setRecordTime(0);
      audioChunksRef.current = [];

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true
      });

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (event) => {
        audioChunksRef.current.push(event.data);
      };

      mediaRecorder.start();
      setIsRecording(true);

      timerRef.current = window.setInterval(() => {
        setRecordTime((prev) => prev + 1);
      }, 1000);
    } catch (err) {
      setError('Unable to access microphone. Please allow audio permissions.');
    }
  };

  const stopRecording = () => {
    if (!isRecording || !mediaRecorderRef.current) {
      setError('Recording has not started yet.');
      return;
    }

    mediaRecorderRef.current.onstop = () => {
      const blob = new Blob(audioChunksRef.current, {
        type: 'audio/webm'
      });
      setAudioBlob(blob);
      setIsRecording(false);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      if (mediaRecorderRef.current.stream) {
        mediaRecorderRef.current.stream.getTracks().forEach((track) => track.stop());
      }
    };

    mediaRecorderRef.current.stop();
  };

  const submitAudio = async () => {
    if (!audioBlob) {
      setError('No recording available to submit.');
      return;
    }

    try {
      setError('');
      setIsSubmitting(true);
      setProcessTime(0);
      processTimerRef.current = window.setInterval(() => {
        setProcessTime((prev) => prev + 1);
      }, 1000);

      const formData = new FormData();
      formData.append('audio', audioBlob, 'recorded.webm');
      formData.append('source_language', sourceLanguage);

      const response = await API.post('/api/emergency/process', formData);
      setResult(response.data);
      setAudioBlob(null);
      setRecordTime(0);
      audioChunksRef.current = [];
    } catch (err) {
      if (err.response) {
        setError(err.response.data.detail || 'Failed to send audio. Please try again.');
      } else if (err.request) {
        setError('Unable to contact the backend server. Please make sure it is running.');
      } else {
        setError(err.message || 'Failed to send audio. Please try again.');
      }
    } finally {
      setIsSubmitting(false);
      if (processTimerRef.current) {
        clearInterval(processTimerRef.current);
        processTimerRef.current = null;
      }
    }
  };

  return (
    <div className='container mt-5'>
      <div className='card shadow-lg border-0 p-4'>
        <h2 className='fw-bold mb-4'>Record Audio</h2>

        {error && <div className='alert alert-danger'>{error}</div>}

        <label className='form-label' htmlFor='record-source-language'>Source language</label>
        <select
          id='record-source-language'
          className='form-select mb-3'
          value={sourceLanguage}
          onChange={(event) => setSourceLanguage(event.target.value)}
          disabled={isRecording || isSubmitting}
        >
          <option value='Auto'>Detect automatically</option>
          <option value='Urdu'>Urdu</option>
          <option value='Pashto'>Pashto</option>
          <option value='Punjabi'>Punjabi</option>
          <option value='English'>English</option>
        </select>

        {!isRecording && !audioBlob && (
          <button className='btn btn-success me-3' onClick={startRecording}>
            Start Recording
          </button>
        )}

        {isRecording && (
          <button className='btn btn-danger' onClick={stopRecording}>
            Stop Recording
          </button>
        )}

        {isRecording && (
          <div className='mt-3'>
            <strong>Recording:</strong> {formatTime(recordTime)}
          </div>
        )}

        {audioBlob && (
          <div className='mt-4'>
            <div className='mb-3'>
              <strong>Recorded Audio:</strong> {formatTime(recordTime)}
            </div>
            <audio controls src={URL.createObjectURL(audioBlob)} className='w-100 mb-3' />
            <button
              className='btn btn-primary'
              onClick={submitAudio}
              disabled={isSubmitting}
            >
              {isSubmitting ? 'Processing...' : 'Submit Audio for Translation'}
            </button>
            <button
              className='btn btn-secondary ms-2'
              onClick={() => {
                setAudioBlob(null);
                setRecordTime(0);
                audioChunksRef.current = [];
                setResult(null);
                setError('');
              }}
              disabled={isSubmitting}
            >
              Record Again
            </button>
            {isSubmitting && (
              <div className='mt-3'>
                <strong>Processing time:</strong> {formatTime(processTime)}
              </div>
            )}
          </div>
        )}

        <ResultCard result={result} />
      </div>
    </div>
  );
};

export default RecordAudio;