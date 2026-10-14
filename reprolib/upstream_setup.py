"""Keep installer preferences inside the selected root; upstream source is unchanged."""
from pathlib import Path
import argparse
import importlib.util
import sys

if __name__=='__main__':
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--strata-dir',required=True)
    parser.add_argument('--local-settings',required=True)
    args,remaining=parser.parse_known_args()
    spec=importlib.util.spec_from_file_location('upstream_setup',Path(args.strata_dir)/'setup.py')
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    module.settings_path=lambda:Path(args.local_settings)
    sys.argv=[str(Path(args.strata_dir)/'setup.py'),*remaining]
    sys.exit(module.main())
