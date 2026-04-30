# DNEMIS Data Import and Export Tool

A modern, user-friendly DHIS2 data import/export and comparison tool for Nigerian school census forms. Built with Python (Flask), this app replaces legacy PHP workflows and provides robust, school-friendly data management for education sector reporting.

## Features

- **DHIS2 Integration**: Secure login, dataset/period selection, and data value sync with DHIS2 API.
- **School-Aware UI**: Auto-selects the correct census form based on school type (PRY, JSS, SSS, PVT, TVET, IQS).
- **Sectioned Data Comparison**: Data elements are grouped by DHIS2 section for clarity.
- **Export/Import**: Download comparison tables as Excel/CSV, import local values from spreadsheet.
- **Push to DHIS2**: Edit and push only changed or missing values.
- **Sync History**: View a log of all syncs with status badges.
- **Local Storage**: SQLite backend for local value caching and sync logs.
- **Robust Fallbacks**: Handles DHIS2 metadata drift, UID/name mismatches, and supports supplemental export files.

## Quick Start

1. **Clone the repo**
   ```sh
   git clone https://github.com/Smalljoe1/DNEMIS_Data_Import-and-Export-Tool.git
   cd DNEMIS_Data_Import-and-Export-Tool
   ```
2. **Install dependencies**
   ```sh
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   pip install -r requirements.txt
   ```
3. **Run the app**
   ```sh
   python app.py
   ```
   The app runs on [http://localhost:5000](http://localhost:5000)

## Usage

- **Login**: Use your DHIS2 school credentials.
- **Census Form Selection**: The app auto-selects the correct form for your school type, or pick manually.
- **Period**: Enter the reporting year (e.g., 2024).
- **Compare/Edit**: Review DHIS2 vs local values, grouped by section. Edit local values as needed.
- **Save/Push**: Save locally or push selected changes to DHIS2.
- **Import/Export**: Download Excel/CSV for offline work, or import updated values.
- **Sync Logs**: Review all previous syncs and their results.

## File Structure

- `app.py` — Flask backend, session/auth, main logic
- `dhis2.py` — DHIS2 API client, metadata, and value fetch
- `db.py` — SQLite helpers for local values and logs
- `export_maps.py` — Optional CSV export fallback logic
- `templates/index.html` — Main frontend UI
- `dataset_exports/` — Place supplemental export CSVs here (see README.txt inside)

## Advanced

- **Supplemental Export Files**: Place files as `dataset_exports_<UID>.csv` in the project root for fallback mapping.
- **Environment Variables**: See `app.py` for optional config (e.g., `FLASK_SECRET_KEY`).

## Contributing

Pull requests and issues are welcome! Please describe your use case or bug clearly.

## License

MIT License

---

**Maintainer:** [Smalljoe1](https://github.com/Smalljoe1)