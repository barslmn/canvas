# Canvas

<div align="center">
  <img src="canvas/static/canvas/img/canvas_logo.png" alt="Canvas Logo" width="400"/>
  <br/>
  <br/>
</div>

Canvas is a Django-based web application for managing and analyzing chip samples, variants, and generating reports.

## Features

- Chip sample management and analysis
- Report generation and management
- Variant analysis and modal viewing
- CNV (Copy Number Variation) tracking
- Sample search functionality
- Interactive sidebar navigation

## Prerequisites

- Docker
- Docker Compose
- GitHub account with access to the repository

## Installation

1. Clone the repository:
```bash
git clone [repository-url]
cd canvas
```

2. Login to GitHub Container Registry:
```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u $GITHUB_USERNAME --password-stdin
```
Note: You'll need a GitHub Personal Access Token with `read:packages` scope.

3. Start the application using Docker Compose:
```bash
docker compose up -d
```

The application will be available at `http://localhost:8000` (or your configured port).

To stop the application:
```bash
docker compose down
```

## Testing and CI/CD

The project uses GitHub Actions for continuous integration and deployment. On each push to the `release` branch, the following checks are performed:

### Code Quality
- **Flake8**: Checks for syntax errors and warnings
- **Black**: Ensures consistent code formatting
- **isort**: Maintains properly sorted imports

### Django Tests
- Runs the Django test suite using `python manage.py test`
- Tests are executed in a clean environment with each CI run

### Docker Build
After all tests pass:
- Builds the Docker image
- Pushes to GitHub Container Registry (ghcr.io)
- Tags with both `latest` and the commit SHA

To run tests locally:
```bash
# Install test dependencies
pip install flake8 black isort

# Run linting
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
black . --check
isort . --check-only --profile black

# Run Django tests
python manage.py test
```

## Project Structure

- `admin.py` - Django admin configurations
- `models.py` - Database models
- `views.py` - View controllers
- `urls.py` - URL routing
- `templates/` - HTML templates
  - `components/` - Reusable UI components
  - `partials/` - Partial template fragments

## Management Commands

The project includes custom management commands:
- `associate_files` - For file association management

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. 