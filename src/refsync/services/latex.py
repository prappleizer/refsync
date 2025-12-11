"""
LaTeX to text conversion utilities.

This module handles conversion of LaTeX markup commonly found in arXiv titles
and abstracts to readable text. For complex math, we preserve the LaTeX for
MathJax rendering in the browser.
"""

import re
from pylatexenc.latex2text import LatexNodes2Text


# Initialize converter with reasonable defaults
_converter = LatexNodes2Text(
    math_mode='verbatim',  # Keep math as-is for MathJax
    strict_latex_spaces=False,
)


def latex_to_text(text: str) -> str:
    r"""
    Convert LaTeX markup to readable text, preserving math for MathJax.
    
    This function:
    - Converts common LaTeX commands to Unicode (e.g., \alpha -> α)
    - Preserves inline math ($...$) and display math ($$...$$, \[...\]) for MathJax
    - Handles common text formatting commands
    - Cleans up whitespace
    
    Args:
        text: String potentially containing LaTeX markup
    
    Returns:
        Cleaned text with Unicode characters and preserved math
    """
    if not text:
        return text
    
    # Normalize line breaks and whitespace
    text = re.sub(r'\s+', ' ', text)
    
    # Protect math environments before conversion
    math_placeholder = "MATHPLACEHOLDER"
    math_blocks = []
    
    def save_math(match):
        math_blocks.append(match.group(0))
        return f"{math_placeholder}{len(math_blocks) - 1}"
    
    # Save display math first (greedy, must come before inline)
    text = re.sub(r'\$\$(.+?)\$\$', save_math, text, flags=re.DOTALL)
    text = re.sub(r'\\\[(.+?)\\\]', save_math, text, flags=re.DOTALL)
    text = re.sub(r'\\begin\{equation\}(.+?)\\end\{equation\}', save_math, text, flags=re.DOTALL)
    text = re.sub(r'\\begin\{align\*?\}(.+?)\\end\{align\*?\}', save_math, text, flags=re.DOTALL)
    
    # Save inline math
    text = re.sub(r'\$([^\$]+?)\$', save_math, text)
    text = re.sub(r'\\\((.+?)\\\)', save_math, text)
    
    # Convert remaining LaTeX to text
    try:
        text = _converter.latex_to_text(text)
    except Exception:
        # If conversion fails, do basic cleanup
        text = _basic_latex_cleanup(text)
    
    # Restore math blocks
    for i, block in enumerate(math_blocks):
        text = text.replace(f"{math_placeholder}{i}", block)
    
    # Final whitespace cleanup
    text = re.sub(r'\s+', ' ', text).strip()
    
    return text


def _basic_latex_cleanup(text: str) -> str:
    """
    Basic LaTeX cleanup when pylatexenc fails.
    Handles common cases manually.
    """
    # Common Greek letters
    greek = {
        r'\alpha': 'α', r'\beta': 'β', r'\gamma': 'γ', r'\delta': 'δ',
        r'\epsilon': 'ε', r'\zeta': 'ζ', r'\eta': 'η', r'\theta': 'θ',
        r'\iota': 'ι', r'\kappa': 'κ', r'\lambda': 'λ', r'\mu': 'μ',
        r'\nu': 'ν', r'\xi': 'ξ', r'\pi': 'π', r'\rho': 'ρ',
        r'\sigma': 'σ', r'\tau': 'τ', r'\upsilon': 'υ', r'\phi': 'φ',
        r'\chi': 'χ', r'\psi': 'ψ', r'\omega': 'ω',
        r'\Gamma': 'Γ', r'\Delta': 'Δ', r'\Theta': 'Θ', r'\Lambda': 'Λ',
        r'\Xi': 'Ξ', r'\Pi': 'Π', r'\Sigma': 'Σ', r'\Phi': 'Φ',
        r'\Psi': 'Ψ', r'\Omega': 'Ω',
    }
    
    for cmd, char in greek.items():
        text = text.replace(cmd, char)
    
    # Common symbols
    symbols = {
        r'\sim': '~', r'\approx': '≈', r'\neq': '≠', r'\leq': '≤',
        r'\geq': '≥', r'\pm': '±', r'\times': '×', r'\cdot': '·',
        r'\infty': '∞', r'\partial': '∂', r'\nabla': '∇',
        r'\sum': '∑', r'\prod': '∏', r'\int': '∫',
        r'\rightarrow': '→', r'\leftarrow': '←', r'\Rightarrow': '⇒',
        r'\degree': '°', r'\deg': '°',
    }
    
    for cmd, char in symbols.items():
        text = text.replace(cmd, char)
    
    # Remove common formatting commands
    text = re.sub(r'\\textbf\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\textit\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\emph\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\textrm\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    
    # Remove remaining backslash commands (but not math)
    text = re.sub(r'\\[a-zA-Z]+\s*', '', text)
    
    # Clean up braces
    text = text.replace('{', '').replace('}', '')
    
    return text


def has_math(text: str) -> bool:
    """Check if text contains LaTeX math that needs MathJax rendering."""
    if not text:
        return False
    
    patterns = [
        r'\$[^\$]+\$',           # Inline math
        r'\$\$.+?\$\$',          # Display math
        r'\\\(.+?\\\)',          # \( \) inline
        r'\\\[.+?\\\]',          # \[ \] display
        r'\\begin\{equation\}',  # equation environment
        r'\\begin\{align',       # align environment
    ]
    
    return any(re.search(p, text) for p in patterns)
