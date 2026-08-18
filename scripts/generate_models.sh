#!/usr/bin/env bash
set -euo pipefail

datamodel-codegen --input COGNITIVE-RUNTIME-GATEWAY.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/gateway.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
datamodel-codegen --input ONTOLOGY-V0.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/ontology.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp

