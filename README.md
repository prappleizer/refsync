# arXiv Library

A clean, minimalist web application for organizing and managing academic papers from arXiv.

## Features

- **Add papers** by pasting any arXiv URL or ID
- **Organize** papers into shelves and tag them
- **Track** reading status (to-read, read)
- **Search** across titles, abstracts, authors, and notes
- **Filter** by shelf, tags, or reading status
- **Add cover images** to papers for visual identification
- **Notes** for each paper
- **LaTeX rendering** via MathJax for proper display of mathematical notation

## Tech Stack

- **Backend**: FastAPI + SQLite (with abstraction layer for other DBs)
- **Frontend**: Vanilla HTML/CSS/JS + Tailwind CSS + Alpine.js + MathJax
- No heavy frameworks, no build step required

## Setup

1. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the server:
```bash
cd backend
uvicorn main:app --reload
```

4. Open http://localhost:8000 in your browser

## Project Structure

```
arxiv-library/
├── backend/
│   ├── main.py           # FastAPI app entry point
│   ├── config.py         # Settings
│   ├── models.py         # Pydantic models
│   ├── routers/          # API endpoints
│   │   ├── papers.py     # Paper CRUD
│   │   ├── shelves.py    # Shelf management
│   │   └── tags.py       # Tag management
│   ├── services/
│   │   ├── arxiv.py      # arXiv API client
│   │   └── latex.py      # LaTeX processing
│   └── db/
│       ├── base.py       # Abstract repository interfaces
│       └── sqlite.py     # SQLite implementation
├── frontend/
│   ├── static/           # JS, CSS assets
│   └── templates/        # Jinja2 HTML templates
│       ├── base.html     # Base template
│       ├── index.html    # Add paper page
│       ├── library.html  # Browse library
│       └── paper.html    # Paper detail page
├── uploads/              # Cover images
├── library.db            # SQLite database (created on first run)
└── requirements.txt
```

## API Endpoints

### Papers
- `POST /api/papers` - Add paper from arXiv URL
- `GET /api/papers` - List all papers
- `GET /api/papers/search` - Search with filters
- `GET /api/papers/{id}` - Get paper details
- `PATCH /api/papers/{id}` - Update paper metadata
- `DELETE /api/papers/{id}` - Remove paper
- `POST /api/papers/{id}/cover` - Upload cover image
- `DELETE /api/papers/{id}/cover` - Remove cover image

### Shelves
- `GET /api/shelves` - List shelves
- `POST /api/shelves` - Create shelf
- `PATCH /api/shelves/{id}` - Update shelf
- `DELETE /api/shelves/{id}` - Delete shelf
- `GET /api/shelves/{id}/papers` - Papers in shelf

### Tags
- `GET /api/tags` - List tags
- `POST /api/tags` - Create tag
- `PATCH /api/tags/{name}` - Update tag color
- `DELETE /api/tags/{name}` - Delete tag

## Future Directions

- **NASA ADS Integration**: Export BibTeX via ADS API
- **Citation Networks**: Visualize paper citations
- **Author Networks**: See collaboration graphs
- **Import/Export**: Backup and restore library data

## Alpine.js Quick Reference

Alpine.js is used for reactivity. Key directives:
- `x-data` - Declare reactive data
- `x-model` - Two-way bind form inputs
- `x-show` - Conditionally show elements
- `x-on:click` or `@click` - Event handlers
- `x-text` - Set text content
- `x-html` - Set HTML content (used for MathJax)
- `x-for` - Loop over arrays

Example:
```html
<div x-data="{ count: 0 }">
    <button @click="count++">Clicked <span x-text="count"></span> times</button>
</div>
```

## License

MIT
