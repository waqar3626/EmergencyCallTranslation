import HeroBanner from '../components/HeroBanner';

function Home() {
  return (
    <>
      <HeroBanner />

      <div className='container mt-5'>
        <div className='row'>
          <div className='col-md-6'>
            <div className='card shadow-lg border-0 p-4'>
              <h2 className='fw-bold'>Project Overview</h2>
              <p>
                This intelligent NLP-based emergency translation platform
                helps emergency operators communicate with callers speaking
                different languages.
              </p>
              <p>The system supports:</p>
              <ul>
                <li>Urdu</li>
                <li>English</li>
                <li>Punjabi</li>
                <li>Pashto</li>
              </ul>
            </div>
          </div>

          <div className='col-md-6'>
            <div className='card shadow-lg border-0 p-4'>
              <h2 className='fw-bold'>Features</h2>
              <ul>
                <li>Upload Audio Translation</li>
                <li>Voice Recording Translation</li>
                <li>Live Speech Translation</li>
                <li>Emergency Detection</li>
                <li>Real-Time Language Detection</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

export default Home;
