"""ROCm-build workaround: transformers.modeling_utils imports
transformers.loss.loss_utils (line 88) whose vision-loss module chain
(DFineForObjectDetectionLoss et al.) aborts this torch-ROCm build with a
C++ std::logic_error. modeling_utils only consumes LOSS_MAPPING (detection
loss heads — unused by Higgs Audio). Stub the module pre-import."""
import sys, types

_stub = types.ModuleType("transformers.loss.loss_utils")
_stub.LOSS_MAPPING = {}
sys.modules.setdefault("transformers.loss.loss_utils", _stub)
