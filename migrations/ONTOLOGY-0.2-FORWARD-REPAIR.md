# Ontology 0.2 Forward-Repair Procedure

Migration `0004_ontology_0_2.sql` automatically upgrades envelopes, normalizes legacy free-form envelope status to `active` unless it already names a 0.2 universal state, and supplies declared defaults for the two tightened 0.1 shapes: `BuyingParty.decisionAuthorityState=unconfirmed` and `BuyerJourney.territory=unspecified`. The deprecated embedded `Person.endpoints` representation remains readable in 0.2.0 while separate `ContactEndpoint` records are backfilled.

Legacy `EpistemicItem` rows are not reinterpreted automatically. Before migration, export them under tenant scope and convert each discriminator to the corresponding concrete record:

- `evidence` → `Evidence` only when a source reference, digest, retention class, and captured time can be reconstructed;
- `assertion` → `Assertion` only when speaker attribution and source location can be reconstructed;
- `verified_fact` → `VerifiedFact` only when a named predicate verification rule and supporting Evidence exist;
- `inference` → `Inference` only when method/version, inputs, confidence, and expiry exist;
- `memory` → `Memory` only when scope, complete material source links, and compilation time exist.

Rows missing required provenance remain quarantined outside canonical current state until an attributed repair record is supplied. Converted records receive new IDs, retain the old ID in migration evidence, and preserve original effective times. A `Correction` links any replaced semantic item. Rerun schema, tenant-isolation, reconstruction, and projection-fence checks before cutover.
