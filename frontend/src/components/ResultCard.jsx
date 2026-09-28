function ResultCard({ result }) {

  if (!result) return null;

  return (

    <div className='card mt-4 shadow-lg border-0'>

      <div className='card-body'>

        <h4 className='fw-bold mb-4'>Translation Result</h4>

        <p>
          <strong>Original Text:</strong>
          {result.original_text}
        </p>

        <p>
          <strong>Detected Language:</strong>
          {result.detected_language}
        </p>

        <p>
          <strong>Translated Text:</strong>
          {result.translated_text}
        </p>

        <p>
          <strong>Emergency Type:</strong>
          {result.emergency_type}
        </p>

      </div>

    </div>
  );
}

export default ResultCard;