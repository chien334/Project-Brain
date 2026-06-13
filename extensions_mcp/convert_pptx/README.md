# PPTX MCP Server

A Model Context Protocol (MCP) server that provides tools to interact with PPTX files.

## Features

- **Extract Text**: Extract text from a PPTX file.
- **Create Presentation**: Create a new PPTX file with a title and content.

## Installation

### Prerequisites

- Python 3.10 or higher
- pip

### Setup

1. Clone or navigate to the project directory:
```bash
cd mcp-pptx
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
