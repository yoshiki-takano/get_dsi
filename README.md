# Derwent Strength Index (DSI) Fetcher

A Streamlit web application for fetching Derwent Strength Index data from the Clarivate Patents Search API using publication numbers.

## Overview

This tool allows users to:
- Upload patent publication numbers via text file or direct input
- Query the Clarivate Patents Search API for DSI metrics
- Retrieve customizable fields including DSI scores and related metrics
- Download results as CSV with UTF-8 BOM encoding for Excel compatibility

## Features

- **Flexible Input**: Support for both file upload and text input
- **Configurable Fields**: Select which DSI metrics to retrieve (Strength Index, Globalization Score, Influence Score, Success Score, Technical Distinctiveness Score, Average Score, Years Remaining, Age Discount)
- **Intelligent Retry Logic**: Automatic retry with exponential backoff and field chunk reduction on failure
- **Chunk Processing**: Batch processing of publication numbers to handle large datasets
- **Progress Tracking**: Real-time progress indication and detailed logging
- **Session Persistence**: Results persist in session state, allowing CSV download without re-running queries
- **CSV Export**: Download results with proper UTF-8 BOM encoding for Excel

## Requirements

- Python 3.7+
- Streamlit
- pandas
- requests

## Installation

1. Clone this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up your Clarivate API key:
   - **For Streamlit Cloud**: Add `IP_DATA_API` to your secrets
   - **For Local Development**: Set the environment variable:
     ```bash
     set IP_DATA_API=your_api_key  # Windows
     export IP_DATA_API=your_api_key  # Linux/Mac
     ```

## Usage

Run the application:
```bash
streamlit run get_dsi.py
```

The app will open in your browser (typically at `http://localhost:8501`)

### Input

1. **Option A: File Upload**
   - Upload a text file with one publication number per line
   - Lines starting with `#` are treated as comments and ignored

2. **Option B: Text Input**
   - Paste publication numbers directly in the text area
   - One publication number per line

### Configuration

Use the sidebar to configure:

**API Settings**
- X-ApiKey: Your Clarivate API key

**Timeout Settings**
- Connection Timeout: Default 10 seconds
- Read Timeout: Default 90 seconds

**Fields to Retrieve**
- Select which DSI metrics to query (all selected by default)

**Parameters**
- **ID_CHUNK**: Number of publication numbers to process per batch (default: 2000)
- **FIELD_CHUNK**: Number of fields to request per API call (default: 14)
- **MAX_RETRIES**: Maximum retry attempts per batch (default: 4)
- **BACKOFF_BASE**: Base multiplier for exponential backoff (default: 1.0)

### Execution

1. Click **実行** (Run) to start the fetch process
2. Monitor progress in the main area and detailed logs in the sidebar
3. View results in the data table once complete
4. Click **CSVをダウンロード** (Download CSV) to export results

### Clear Results

Click **結果をクリア** (Clear Results) to clear the current results without re-running queries.

## Retry Strategy

When API calls fail, the application automatically retries with:
- Progressive field chunk reduction (14 → 7 → 4 → 2)
- Exponential backoff with jitter
- Maximum sleep duration capped at 15 seconds

This approach handles rate limiting and transient API errors gracefully.

## Output

Results are downloaded as a timestamped CSV file with:
- UTF-8 BOM encoding (compatible with Excel)
- PUBLICATION_NUMBER as the first column
- Selected fields in the order specified
- Additional fields (if any) in alphabetical order
- Nested objects/arrays serialized as JSON

## API Reference

Uses the Clarivate Patents Search API:
- Endpoint: `https://api.clarivate.com/patents/search/`
- Query Type: BASIC search by PUBLICATION_NUMBER
- Response Format: JSON

## Language

The web UI is in Japanese (日本語).

## License

[Add your license information here]

## Support

For issues or questions about the Clarivate Patents Search API, contact Clarivate support.

## Author

Created for Clarivate Analytics patent research workflows.

---

**Note**: Publication number search is currently the only supported search method. Other search methods may be added in future versions.
