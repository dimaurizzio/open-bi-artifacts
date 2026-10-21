"""Los tests no leen .env.local ni .env.prod: cada uno fija la configuración que necesita."""

import os

os.environ["APP_ENV"] = "test"
