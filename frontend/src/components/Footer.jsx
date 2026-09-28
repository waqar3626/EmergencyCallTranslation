import { Link } from 'react-router-dom';

function Footer() {
  return (
    <footer className="bg-dark text-light py-4 mt-5">
      <div className="container">
        <div className="row">
          <div className="col-md-6">
            <p>Project Design by Waqas Hussain</p>
            <p>&copy; {new Date().getFullYear()} All rights reserved to Waqas Hussain</p>
          </div>
          <div className="col-md-6">
            <h5>Quick Links</h5>
            <ul className="list-unstyled">
              <li><Link to="/" className="text-light">Home</Link></li>
              <li><Link to="/upload-audio" className="text-light">Upload File</Link></li>
              <li><Link to="/record-audio" className="text-light">Record Audio</Link></li>
              <li><Link to="/live-translation" className="text-light">Live Translation</Link></li>
              <li><Link to="/contact-us" className="text-light">Contact Us</Link></li>
            </ul>
          </div>
        </div>
      </div>
    </footer>
  );
}

export default Footer;