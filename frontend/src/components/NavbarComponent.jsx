import { Link } from 'react-router-dom';

function NavbarComponent() {
  return (

    <nav className='navbar navbar-expand-lg navbar-dark bg-dark'>

      <div className='container'>

        <Link className='navbar-brand fw-bold' to='/'>
          Live Call Translation
        </Link>

        <button
          className='navbar-toggler'
          type='button'
          data-bs-toggle='collapse'
          data-bs-target='#navbarNav'
        >
          <span className='navbar-toggler-icon'></span>
        </button>

        <div className='collapse navbar-collapse' id='navbarNav'>

          <ul className='navbar-nav ms-auto'>

            <li className='nav-item'>
              <Link className='nav-link' to='/'>Home</Link>
            </li>

            <li className='nav-item'>
              <Link className='nav-link' to='/upload-audio'>Upload File</Link>
            </li>

            <li className='nav-item'>
              <Link className='nav-link' to='/record-audio'>Record Audio</Link>
            </li>

            <li className='nav-item'>
              <Link className='nav-link' to='/live-translation'>Live Translation</Link>
            </li>

            <li className='nav-item'>
              <Link className='nav-link' to='/contact-us'>Contact Us</Link>
            </li>

          </ul>

        </div>

      </div>

    </nav>
  );
}

export default NavbarComponent;