# DOCX MCP Server

A Model Context Protocol (MCP) server that provides tools to interact with DOCX files.

## Features

- **Extract Text**: Extract text from a DOCX file.
- **Extract Tables**: Extract tables from a DOCX file.
- **Create DOCX**: Create a new DOCX file from text.

## Installation

### Prerequisites

- Python 3.10 or higher
- pip

### Setup

1. Clone or navigate to the project directory:
```bash
cd mcp-docx
```

2. Install dependencies:
```bash
pip install -e .
```

Or install with development dependencies:
```bash
pip install -e ".[dev]"
```

## Usage

### Running the Server

Start the MCP server with STDIO transport:

```bash
python server_stdio.py
