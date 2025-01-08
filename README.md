# Canvas

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

## Installation

1. Clone the repository:
```bash
git clone [repository-url]
cd canvas
```

2. Start the application using Docker Compose:
```bash
docker compose up -d
```

The application will be available at `http://localhost:8000` (or your configured port).

To stop the application:
```bash
docker compose down
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