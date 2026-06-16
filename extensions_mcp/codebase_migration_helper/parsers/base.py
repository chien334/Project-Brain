import os

PARSER_REGISTRY = {}

def register_parser(langs):
    def decorator(cls):
        for lang in langs:
            PARSER_REGISTRY[lang] = cls
        return cls
    return decorator

class BaseParser:
    def parse(self, relative_path, code):
        return [], [], {}

def detect_lang(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".py": "python",
        ".java": "java",
        ".cs": "csharp",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".cpp": "cpp",
        ".h": "cpp",
        ".c": "c",
        ".rb": "ruby",
        ".php": "php",
        ".cbl": "cobol",
        ".cob": "cobol",
        ".bas": "vb6",
        ".cls": "vb6",
        ".frm": "vb6",
        ".vbs": "vb6",
        ".pas": "pascal",
        ".dpr": "pascal",
        ".f": "fortran",
        ".for": "fortran",
        ".f90": "fortran",
        ".st": "structured_text",
        ".lsl": "structured_text",
        ".xml": "xml",
        ".lcp": "xml",
        ".lcn": "xml"
    }
    return mapping.get(ext, "text")
