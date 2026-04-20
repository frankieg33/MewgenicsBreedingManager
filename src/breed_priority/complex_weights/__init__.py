"""Complex Weights sub-package — custom scoring rules based on cat conditions."""

from .model import ComplexWeight, Condition, LOGIC_AND, LOGIC_OR
from .evaluator import evaluate_cw, compute_cw_matches, build_cat_trait_set
from .dialog import ComplexWeightsDialog
