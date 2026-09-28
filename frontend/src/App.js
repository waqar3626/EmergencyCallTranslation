import { BrowserRouter, Routes, Route } from 'react-router-dom';

import NavbarComponent from './components/NavbarComponent';
import Footer from './components/Footer';

import Home from './pages/Home';
import UploadAudio from './pages/UploadAudio';
import RecordAudio from './pages/RecordAudio';
import LiveTranslation from './pages/LiveTranslation';
import ContactUs from './pages/ContactUs';

function App() {
  return (
    <BrowserRouter>

      <NavbarComponent />

      <Routes>
        <Route path='/' element={<Home />} />
        <Route path='/upload-audio' element={<UploadAudio />} />
        <Route path='/record-audio' element={<RecordAudio />} />
        <Route path='/live-translation' element={<LiveTranslation />} />
        <Route path='/contact-us' element={<ContactUs />} />
      </Routes>

      <Footer />

    </BrowserRouter>
  );
}

export default App;