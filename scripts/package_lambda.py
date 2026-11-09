import shutil
import sys
import sysconfig
import zipfile
from pathlib import Path


project_root = Path(__file__).resolve().parent.parent
package_root = Path("/tmp/rag-lambda-package")
archive_path = project_root / "ingest.zip"

if package_root.exists():
    shutil.rmtree(package_root)
package_root.mkdir(parents=True)

shutil.copy2(project_root / "src/lambda_func/handler.py", package_root / "handler.py")
src_package = package_root / "src"
src_package.mkdir()
for module in ("__init__.py", "config.py", "embeddings.py", "search.py"):
    shutil.copy2(project_root / "src" / module, src_package / module)

site_packages = Path(sysconfig.get_paths()["purelib"])
shutil.copytree(site_packages / "pypdf", package_root / "pypdf")

with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in package_root.rglob("*"):
        if path.is_file():
            archive.write(path, path.relative_to(package_root))

shutil.rmtree(package_root)
print(f"Created {archive_path} for local Lambda deployment.")
