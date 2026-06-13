---
name: excel-md-converter
description: Expert in converting between Excel/CSV and Markdown formats using the excel-to-md MCP server. Use when working with spreadsheet data that needs to be converted to/from Markdown tables.
license: MIT
---

# Excel ↔ Markdown Converter Skill

## Overview

This skill enables seamless conversion between Excel/CSV files and Markdown tables using the `excel-to-md` MCP server. Perfect for documentation, data analysis, and content management workflows.

---

# Capabilities

## 1. Excel/CSV → Markdown Conversion

Convert spreadsheet data into clean, readable Markdown tables.

**Use Cases:**
- Generate documentation from data
- Create reports in Markdown format
- Export data for GitHub/GitLab wikis
- Prepare data for static site generators

**Available Tools:**
- `excel_list_sheets` - List all sheets in a file
- `excel_convert_to_markdown` - Convert entire workbook or specific sheets
- `excel_convert_sheet_to_markdown` - Convert single sheet with pagination support

## 2. Markdown → Excel Conversion

Convert Markdown tables back into Excel files for further analysis.

**Use Cases:**
- Import documentation data into spreadsheets
- Convert Markdown reports to Excel
- Create Excel files from text-based data
- Batch process Markdown tables

**Available Tools:**
- `markdown_convert_to_excel` - Convert Markdown tables to Excel file

---

# Workflow Patterns

## Pattern 1: Quick Excel to Markdown

```
1. List sheets to understand structure
2. Convert all sheets or specific ones
3. Use the Markdown output in documentation
```

**Example:**
```
User: "Convert sales.xlsx to Markdown"

Steps:
1. Use excel_list_sheets to see available sheets
2. Use excel_convert_to_markdown to convert all sheets
3. Present formatted Markdown tables
```

## Pattern 2: Large File Processing

```
1. List sheets to identify target
2. Use pagination for large sheets
3. Process in chunks to avoid memory issues
```

**Example:**
```
User: "Convert the first 1000 rows of Data sheet from large.xlsx"

Steps:
1. Use excel_convert_sheet_to_markdown with row_limit=1000
2. Check has_more flag for additional pages
3. Optionally fetch more pages with row_offset
```

## Pattern 3: Markdown to Excel Workflow

```
1. Prepare or receive Markdown content
2. Validate format (## headings + tables)
3. Convert to Excel file
4. Confirm creation with file details
```

**Example:**
```
User: "Create an Excel file from this Markdown table"

Steps:
1. Validate Markdown has proper format (## Sheet Name + tables)
2. Use markdown_convert_to_excel with content and output path
3. Confirm file creation with sheet count and names
```

## Pattern 4: Round-trip Conversion

```
1. Excel → Markdown for editing
2. Edit Markdown content
3. Markdown → Excel for distribution
```

**Example:**
```
User: "Convert report.xlsx to Markdown, let me edit it, then create a new Excel file"

Steps:
1. Convert Excel to Markdown
2. Present Markdown for user editing
3. After edits, convert back to Excel
4. Provide both original and new file paths
```

---

# Tool Usage Guidelines

## excel_list_sheets

**When to use:**
- Before converting to understand file structure
- To verify sheet names before specific conversion
- To provide user with sheet options

**Parameters:**
- `file_path` (required): Path to Excel/CSV file

**Returns:** JSON with file path, sheet count, and sheet names

## excel_convert_to_markdown

**When to use:**
- Converting entire workbook
- Converting specific sheets by name
- Quick conversion without pagination needs

**Parameters:**
- `file_path` (required): Path to Excel/CSV file
- `sheets` (optional): Array of sheet names to convert

**Returns:** Markdown text with all requested sheets

**Best Practices:**
- List sheets first if unsure of names
- Use for files with reasonable size (< 10,000 rows total)
- Each sheet becomes a level-2 heading (##)

## excel_convert_sheet_to_markdown

**When to use:**
- Large files requiring pagination
- Need to know total row count
- Processing data in chunks

**Parameters:**
- `file_path` (required): Path to Excel/CSV file
- `sheet_name` (required): Name of sheet to convert
- `row_offset` (optional): Skip N data rows (default: 0)
- `row_limit` (optional): Max rows to return (default: all)

**Returns:** JSON with markdown, metadata, and pagination info

**Best Practices:**
- Use row_limit for files > 1,000 rows
- Check has_more flag to determine if more pages exist
- Increment row_offset by row_limit for next page

## markdown_convert_to_excel

**When to use:**
- Converting Markdown tables to Excel
- Creating Excel from documentation
- Batch processing Markdown data

**Parameters:**
- `markdown_content` (required): Markdown text with ## headings and tables
- `output_file_path` (required): Path for output Excel file (.xlsx or .xlsm)

**Returns:** JSON with file path, sheet count, and sheet names

**Best Practices:**
- Ensure Markdown follows format: `## Sheet Name` + table
- Use .xlsx extension for compatibility
- Check for proper table separators (| --- |)
- Verify output path is writable

---

# Markdown Format Specification

## Required Format

```markdown
## Sheet Name
| Header1 | Header2 | Header3 |
| --- | --- | --- |
| Value1 | Value2 | Value3 |
| Value4 | Value5 | Value6 |
```

## Key Rules

1. **Sheet Names**: Use level-2 headings (`##`)
2. **Table Structure**: 
   - First row = headers
   - Second row = separator (`| --- |`)
   - Remaining rows = data
3. **Cell Content**:
   - Pipes in content must be escaped: `\|`
   - Newlines are converted to spaces
4. **Multiple Sheets**: Separate with blank lines

## Example Multi-Sheet

```markdown
## Sales Data
| Product | Q1 | Q2 |
| --- | --- | --- |
| Widget | 100 | 150 |
| Gadget | 200 | 250 |

## Inventory
| Item | Stock | Location |
| --- | --- | --- |
| Widget | 50 | Warehouse A |
| Gadget | 75 | Warehouse B |
```

---

# Error Handling

## Common Errors and Solutions

### File Not Found
**Error:** `Error: File not found: <path>`
**Solution:** Verify file path is correct and file exists

### Invalid Sheet Name
**Error:** `Error: Sheet "X" not found. Available: [...]`
**Solution:** Use excel_list_sheets to get correct names

### Invalid File Type
**Error:** `Error: Unsupported file type '.doc'`
**Solution:** Use .xlsx, .xlsm, .xltx, .xltm, or .csv files

### Invalid Markdown Format
**Error:** `Error: No valid Markdown tables found`
**Solution:** Ensure format has `## Sheet Name` followed by proper table

### Permission Denied
**Error:** `Error: Permission denied writing to '<path>'`
**Solution:** Check file is not open, verify write permissions

### Invalid Output Extension
**Error:** `Error: Output file must have .xlsx or .xlsm extension`
**Solution:** Use .xlsx or .xlsm for output file path

---

# Best Practices

## Performance

1. **Large Files**: Use pagination with `excel_convert_sheet_to_markdown`
2. **Multiple Sheets**: Convert specific sheets instead of entire workbook
3. **Memory**: Process large files in chunks (1000-5000 rows per chunk)

## Data Quality

1. **Verify Structure**: Always list sheets before conversion
2. **Check Output**: Validate Markdown format before converting back
3. **Escape Special Characters**: Ensure pipes are escaped in cell content
4. **Handle Empty Cells**: Empty cells become empty strings

## User Experience

1. **Provide Context**: Show sheet names and row counts
2. **Confirm Actions**: Display file paths and success messages
3. **Handle Errors Gracefully**: Provide actionable error messages
4. **Offer Options**: Let users choose specific sheets when multiple exist

---

# Integration Examples

## With Documentation Workflow

```
1. Export data from Excel to Markdown
2. Include in documentation (README, wiki, etc.)
3. Update data in Excel
4. Re-export to keep docs in sync
```

## With Data Analysis

```
1. Receive Markdown data from API/web
2. Convert to Excel for analysis
3. Process in Excel (formulas, charts)
4. Export results back to Markdown for sharing
```

## With Version Control

```
1. Store data as Markdown in Git
2. Convert to Excel for local work
3. Make changes in Excel
4. Convert back to Markdown for commit
```

---

# Quick Reference

## Supported File Types

| Type | Read | Write |
|------|------|-------|
| .xlsx | ✅ | ✅ |
| .xlsm | ✅ | ✅ |
| .xltx | ✅ | ❌ |
| .xltm | ✅ | ❌ |
| .csv | ✅ | ❌ |

## Tool Selection Matrix

| Task | Tool |
|------|------|
| List sheets | `excel_list_sheets` |
| Convert small file | `excel_convert_to_markdown` |
| Convert large file | `excel_convert_sheet_to_markdown` |
| Convert specific sheets | `excel_convert_to_markdown` |
| Paginate results | `excel_convert_sheet_to_markdown` |
| Markdown → Excel | `markdown_convert_to_excel` |

## Common Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| file_path | string | required | Path to Excel/CSV file |
| sheets | array | null | Sheet names to convert |
| sheet_name | string | required | Single sheet name |
| row_offset | integer | 0 | Skip N data rows |
| row_limit | integer | null | Max rows to return |
| markdown_content | string | required | Markdown text |
| output_file_path | string | required | Output Excel path |
