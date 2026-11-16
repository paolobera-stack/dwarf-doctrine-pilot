# Blueprints

Blueprint [Quickfort](https://docs.dfhack.org/en/stable/docs/guides/quickfort-user-guide.html) (CSV) usati dalla coda di espansione della dottrina.

- `catalog.json` descrive ogni blueprint: file, dimensioni, celle da scavare, materiali ed effetti (usati dal mock server).
- La dottrina riferisce i blueprint per id (`blueprint`) e li applica **solo** nelle zone designate dall'utente (`zone_id`).
- Con il bridge DFHack reale l'autopilota eseguirà `quickfort run <file> -n /<label> --cursor x,y,z` sulle coordinate della zona.
