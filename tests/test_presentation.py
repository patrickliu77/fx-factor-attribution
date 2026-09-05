import json
import shutil
import subprocess

import pytest

from fxdash.narrative.trigger import TRIGGER_RESIDUAL_BP, TRIGGER_Z
from fxdash.web.app import STATIC_DIR


def test_display_sorting_and_residual_thresholds():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node needed for JavaScript display helpers")
    uri = (STATIC_DIR / "presentation.js").as_uri()
    script = f'''import {{rankedContributions, residualFlag}} from {json.dumps(uri)};
      import assert from 'node:assert/strict';
      assert.deepEqual(rankedContributions({{a:1,b:-4,c:null,d:NaN,e:0}}), [['b',-4],['a',1]]);
      assert.deepEqual(rankedContributions(null), []);
      assert.equal(residualFlag({{residual:{TRIGGER_RESIDUAL_BP}/1e4,residual_z:{TRIGGER_Z}}}), true);
      assert.equal(residualFlag({{residual:-.005,residual_z:-2}}), true);
      assert.equal(residualFlag({{residual:.00499,residual_z:9}}), false);
      assert.equal(residualFlag({{residual:.1,residual_z:1.99}}), false);
      assert.equal(residualFlag({{residual:.1,residual_z:null}}), false);
    '''
    subprocess.run([node, "--input-type=module", "-e", script], check=True,
                   capture_output=True, text=True)
