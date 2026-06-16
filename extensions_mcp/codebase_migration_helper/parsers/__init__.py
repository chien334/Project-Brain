# Package for modular language parsers
from .base import PARSER_REGISTRY, BaseParser, detect_lang
from .python import PythonParser
from .cobol import CobolParser
from .vb6 import VB6Parser
from .pascal import PascalParser
from .fortran import FortranParser
from .st_xml import StructuredTextParser
from .generic import GenericParser
