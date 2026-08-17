"""Agent D workflow implementations."""

from .wf01 import WF01SingleTarget
from .wf02 import WF02MultiplexPanel
from .wf03 import WF03InternalControl
from .wf04 import WF04ExistingPrimerValidation
from .wf05 import WF05VariationTolerance
from .wf06 import WF06CompletePanel
from .wf07 import WF07PrimerDiagnosis

__all__ = ["WF01SingleTarget", "WF02MultiplexPanel", "WF03InternalControl", "WF04ExistingPrimerValidation", "WF05VariationTolerance", "WF06CompletePanel", "WF07PrimerDiagnosis"]
