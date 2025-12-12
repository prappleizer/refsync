# RefSync

A citation manager for astronomers with seamless NASA ADS integration.

![RefSync Logo](docs/logo.png)

## Features

- **Paper Library**: Import papers from arXiv, organize with shelves and tags [coming soon: import from ADS]. Explore papers via cards, lists, multifactor filtering, or search across titles, abstracts, authors, or personal notes. 
- **Image Headers**: Attach (soon: choose) figures or images from papers to serve as card headers, for finding that one plot you remember
- **ADS Sync**: One-click sync to update arXiv preprints with published journal info: any preprints published to journal in the interim get new citation bibtex with updated information
- **BibTeX Export**: Export citations in bibtex form for individual papers, collections, or any filtered set of papers, either to clipboard or `.bib` file. Bibtex are referenced via convenient Author:Year format.
- **Dark Mode**: Easy on the eyes for those late-night paper sessions
- **Starred Papers**: Mark important papers for quick access

## Installation

Install from source:

Recommended: create an environment: 
```
mamba create -n refsync pip
mamba activate refsync
```

```bash
git clone https://github.com/prappleizer/refsync.git
cd refsync
pip install -e .
```

## Quick Start

```bash
# Start the server (opens browser automatically)
refsync

# Or specify host/port
refsync --host 0.0.0.0 --port 8080

# Don't open browser
refsync --no-browser
```

Then visit http://localhost:8000 in your browser.

## Configuration

### Data Location

By default, RefSync stores data (e.g., your personal library) in `~/.refsync/`. You can change this:

```bash
export REFSYNC_DATA_DIR=/path/to/your/data
refsync
```
(or update your shell profile for permanent changes).

### NASA ADS API Key

To sync citations with NASA ADS:

1. Get a free API key from [ADS](https://ui.adsabs.harvard.edu/user/settings/token)
2. Go to Settings in RefSync
3. Enter your API key (stored encrypted locally)

## Usage

### Adding Papers

1. Go to "Add Paper"
2. Paste an arXiv URL or ID (e.g., `2301.07041` or `https://arxiv.org/abs/2301.07041`)
3. Click "Fetch" to preview
4. Add to shelves, tags, set reading status. Optionally upload an image to represent the paper in card headers.
5. Click "Add to Library"

### Library 
Your library is your home for all the papers you've added.
![library](docs/library.png)

You can view papers as cards (notice how some have had image headers added!) or in list form:
![list](docs/list.png)

At the top is a search bar that lets you filter (character by character) on title, abstract, authors, or notes you have made for individual papers. You can also filter by starred papers, by shelf, by tag, by status (read vs. unread) or by any combination therein.

When you hover over a card/list item, you get a preview popup of the abstract:

![hover](docs/hover.png)

And clicking on a paper will take you to its page, where you can update tags/shelves, add notes, and access the pdf of the paper (either on arxiv or in your offline downloaded library).

![paperpage](docs/paper.png)

You can also export the paper bibtex --- or, returning to the library, we can export bibtex for any list of papers, either by filtering down to a set we want using the various options, or by checking the papers to include (or both --- if you check any papers, only those will be included). 

![export](docs/export.png)



## Syncing with ADS

A major feature of `refsync` is, naturally, the sync feature. Using this, you can keep your library synced with NASA ADS. That way, if an e-print gets published in a journal, the updated ADS reference can be pulled and integrated into your citation library --- then upon export, you will have all published papers with their full journal citations, while e-print only papers reserve their arxiv bibtex. 

To set this up:

1. Configure your ADS API key in Settings
2. Go to Library
3. Click "Sync with ADS"
4. Papers that have been published will be updated with journal info

Coming Soon: Settings to allow your `refsync` app to auto-sync with ADS every N hours/days.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [NASA ADS](https://ui.adsabs.harvard.edu/) for their excellent API
- [arXiv](https://arxiv.org/) for open access to preprints