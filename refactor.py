import os
import re

router_dir = "backend/routers"

import_snippet = """
import traceback
import logging

logger = logging.getLogger(__name__)
"""

for filename in os.listdir(router_dir):
    if filename.endswith(".py") and filename != "auth.py" and filename != "__init__.py":
        filepath = os.path.join(router_dir, filename)
        with open(filepath, 'r') as f:
            content = f.read()

        # Add imports if not present
        if "import traceback" not in content:
            content = content.replace("from fastapi import APIRouter", f"from fastapi import APIRouter{import_snippet}")

        # Replace except Exception as e: with logging
        except_block = """    except Exception as e:
        logger.error(f"Error in {filename}: {str(e)}")
        logger.error(traceback.format_exc())"""
        
        content = re.sub(r'    except Exception as e:', except_block, content)

        with open(filepath, 'w') as f:
            f.write(content)

print("Refactoring complete.")
