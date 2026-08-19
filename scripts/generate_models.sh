#!/usr/bin/env bash
set -euo pipefail
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/buyer-ops-uv-cache}"

uv run datamodel-codegen --input COGNITIVE-RUNTIME-GATEWAY.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/gateway.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
uv run datamodel-codegen --input GATEWAY-RUNTIME-CONFIG.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/gateway_runtime.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
uv run datamodel-codegen --input ONTOLOGY-V0.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/ontology.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
uv run datamodel-codegen --input HABITAT-EFFECT.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/habitat.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
uv run datamodel-codegen --input TEMPORAL-WORKFLOW.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/temporal.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
uv run datamodel-codegen --input CONTEXT-COMPILER.schema.json --input-file-type jsonschema \
  --output src/buyer_ops_contracts/generated/context.py --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 --use-standard-collections --use-union-operator \
  --strict-nullable --use-subclass-enum --disable-timestamp
for specification in \
  'OPEN-025-027.schema.json:authority_activation_fair_housing' \
  'OPEN-019-024.schema.json:closure' \
  'OPERATOR-SURFACE.schema.json:operator_surface' \
  'TELEMETRY-SLO.schema.json:telemetry_slo' \
  'OT01-INGRESS.schema.json:ot01_ingress' \
  'CONNECTOR-GATEWAY.schema.json:connector_gateway' \
  'RELEASE-ACTIVATION.schema.json:release_activation'
do
  schema="${specification%%:*}"
  module="${specification##*:}"
  uv run datamodel-codegen --input "$schema" --input-file-type jsonschema \
    --output "src/buyer_ops_contracts/generated/$module.py" --output-model-type pydantic_v2.BaseModel \
    --target-python-version 3.12 --use-standard-collections --use-union-operator \
    --strict-nullable --use-subclass-enum --disable-timestamp
done
