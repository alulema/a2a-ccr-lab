# CCR — Evidencia experimental (generada por scripts/local_run.py)

## RQ1 — Efectividad (V=5, kappa=0.2, pool=mixed, canary indistinguible)

| policy | success@1 | net_utility | incident_rate | p95_ms | canaries |
|---|---|---|---|---|---|
| ccr | 0.9514 | 2.5333 | 0.0903 | 1011 | 4 |
| optimistic_fallback | 0.9583 | 1.8889 | 0.4861 | 928 | 0 |
| attestation_only | 0.5139 | 1.5694 | 0 | 635 | 0 |
| card_only | 0.5139 | 1.5694 | 0 | 635 | 0 |
| embedding | 0.4722 | 1.1944 | 0.0833 | 635 | 0 |
| random | 0.4722 | 0.5556 | 0.4028 | 619 | 0 |


## RQ2 — ¿Cuándo compensa verificar? (pool=mixed, indist=True)

Utilidad neta media por celda; **W** marca al ganador. Buscamos la región donde CCR supera a optimistic_fallback y attestation_only.

| V \ kappa | k=0.05 | k=0.1 | k=0.2 | k=0.4 | k=0.6 |
|---|---|---|---|---|---|
| V=1 | CCR=-0.69 OPT=-1.94 ATT=-0.49 → **ATT** | CCR=-0.87 OPT=-1.94 ATT=-0.49 → **ATT** | CCR=-1.27 OPT=-1.94 ATT=-0.49 → **ATT** | CCR=-1.96 OPT=-1.96 ATT=-0.49 → **ATT** | CCR=-1.96 OPT=-1.97 ATT=-0.49 → **ATT** |
| V=2 | CCR=0.30 OPT=-0.99 ATT=0.03 → **CCR** | CCR=0.08 OPT=-0.99 ATT=0.03 → **CCR** | CCR=-0.32 OPT=-0.99 ATT=0.03 → **ATT** | CCR=-1.12 OPT=-0.97 ATT=0.03 → **ATT** | CCR=-2.13 OPT=-0.99 ATT=0.03 → **ATT** |
| V=5 | CCR=3.26 OPT=1.89 ATT=1.57 → **CCR** | CCR=2.93 OPT=1.89 ATT=1.57 → **CCR** | CCR=2.53 OPT=1.89 ATT=1.57 → **CCR** | CCR=1.73 OPT=1.99 ATT=1.57 → **OPT** | CCR=1.06 OPT=1.97 ATT=1.57 → **OPT** |
| V=10 | CCR=8.19 OPT=6.68 ATT=4.14 → **CCR** | CCR=7.69 OPT=6.68 ATT=4.14 → **CCR** | CCR=7.29 OPT=6.68 ATT=4.14 → **CCR** | CCR=6.49 OPT=6.92 ATT=4.14 → **OPT** | CCR=5.99 OPT=6.90 ATT=4.14 → **OPT** |

CCR gana en **8/20** celdas (típicamente V alto / kappa bajo).


## RQ3 — Robustez ante degradación (pool=clean, V=5, k=0.2)

attestation_only confía en cards firmadas válidas; el agente honesto pero **degradado** está firmado y la card está obsoleta → lo elige y falla.

| policy | success@1 | net_utility | p95_ms |
|---|---|---|---|
| ccr | 1 | 3.1569 | 981 |
| card_only | 0.5139 | 1.5694 | 635 |
| attestation_only | 0.5139 | 1.5694 | 635 |
| optimistic_fallback | 1 | 3.4722 | 809 |


## RQ4 — Defección canary-aware y mitigación (pool=mixed, V=5, k=0.2)

El defector pasa el canary y falla la tarea real **solo si puede distinguir la prueba**. Hacer el canary indistinguible (I) lo neutraliza vs. distinguible (D).

| configuración CCR | defection_rate | incident_rate | success@1 |
|---|---|---|---|
| canary distinguible (D) — naive | 0.875 | 0.9931 | 0.7569 |
| canary indistinguible (I) — mitigación | 0.0556 | 0.0903 | 0.9514 |


_Nota: números deterministas; el despliegue en Azure los reproduce sobre A2A real._