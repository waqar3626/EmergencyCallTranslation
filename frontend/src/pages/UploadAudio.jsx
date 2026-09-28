import { useState } from 'react';

import API from '../services/api';
import ResultCard from '../components/ResultCard';

function UploadAudio() {

  const [audio, setAudio] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [sourceLanguage, setSourceLanguage] = useState('Auto');

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!audio) {
      setError('Please select an audio file to translate.');
      return;
    }

    setError('');
    setResult(null);
    setIsSubmitting(true);

    const formData = new FormData();
    formData.append('audio', audio);
    formData.append('source_language', sourceLanguage);

    try {
      const response = await API.post('/api/emergency/process', formData);
      setResult(response.data);
    } catch (err) {
      if (err.response) {
        setError(err.response.data.detail || `Server error ${err.response.status}`);
      } else if (err.request) {
        setError('Unable to contact the backend server. Please make sure it is running and try again.');
      } else {
        setError(err.message || 'An unexpected error occurred.');
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (

    <div className='container mt-5'>

      <div className='card shadow-lg border-0 p-4'>

        <h2 className='fw-bold mb-4'>Upload Audio File</h2>

        <form onSubmit={handleSubmit} encType='multipart/form-data'>

          <input
            type='file'
            className='form-control'
            accept='audio/*,.mp3,.wav,.m4a,.ogg,.opus,.webm,.weba,.aac,.flac,.amr'
            onChange={(e) => {
              setAudio(e.target.files[0]);
              setResult(null);
              setError('');
            }}
          />

          <label className='form-label mt-3' htmlFor='upload-source-language'>Source language</label>
          <select
            id='upload-source-language'
            className='form-select'
            value={sourceLanguage}
            onChange={(e) => setSourceLanguage(e.target.value)}
          >
            <option value='Auto'>Detect automatically</option>
            <option value='Urdu'>Urdu</option>
            <option value='Pashto'>Pashto</option>
            <option value='Punjabi'>Punjabi</option>
            <option value='English'>English</option>
          </select>

          <button className='btn btn-primary mt-3' type='submit' disabled={!audio || isSubmitting}>
            {isSubmitting ? 'Translating...' : 'Translate Audio'}
          </button>

        </form>

        {error && <div className='alert alert-danger'>{error}</div>}
        <ResultCard result={result} />

      </div>

    </div>
  );
}

export default UploadAudio;