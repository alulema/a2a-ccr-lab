# CCR — Evidencia experimental (generada por scripts/local_run.py)

## RQ1 — Efectividad (V=5, kappa=0.2, pool=mixed, canary indistinguible)

| policy | success@1 | net_utility | incident_rate | p95_ms | canaries |
|---|---|---|---|---|---|
| ccr | 0.9444 | 2.3458 | 0.1389 | 1008 | 4 |
| embedding | 0.5556 | 1.7778 | 0 | 638 | 0 |
| optimistic_fallback | 0.9722 | 1.4167 | 0.7222 | 930 | 0 |
| random | 0.5139 | 0.75 | 0.4097 | 610 | 0 |
| attestation_only | 0.3472 | -0.1667 | 0.4514 | 635 | 0 |
| card_only | 0.3472 | -0.1667 | 0.4514 | 635 | 0 |


## RQ2 — ¿Cuándo compensa verificar? (pool=mixed, indist=True)

Utilidad neta media por celda; **W** marca al ganador. Buscamos la región donde CCR supera a optimistic_fallback y attestation_only.

| V \ kappa | k=0.05 | k=0.1 | k=0.2 | k=0.4 | k=0.6 |
|---|---|---|---|---|---|
| V=1 | CCR=-0.84 OPT=-2.47 ATT=-1.56 → **CCR** | CCR=-1.03 OPT=-2.47 ATT=-1.56 → **CCR** | CCR=-1.43 OPT=-2.47 ATT=-1.56 → **CCR** | CCR=-2.47 OPT=-2.47 ATT=-1.56 → **ATT** | CCR=-2.47 OPT=-2.47 ATT=-1.56 → **ATT** |
| V=2 | CCR=0.15 OPT=-1.50 ATT=-1.21 → **CCR** | CCR=-0.09 OPT=-1.50 ATT=-1.21 → **CCR** | CCR=-0.49 OPT=-1.50 ATT=-1.21 → **CCR** | CCR=-1.29 OPT=-1.47 ATT=-1.21 → **ATT** | CCR=-2.67 OPT=-1.47 ATT=-1.21 → **ATT** |
| V=5 | CCR=3.13 OPT=1.42 ATT=-0.17 → **CCR** | CCR=2.75 OPT=1.42 ATT=-0.17 → **CCR** | CCR=2.35 OPT=1.42 ATT=-0.17 → **CCR** | CCR=1.55 OPT=1.53 ATT=-0.17 → **CCR** | CCR=0.93 OPT=1.53 ATT=-0.17 → **OPT** |
| V=10 | CCR=8.10 OPT=6.28 ATT=1.57 → **CCR** | CCR=7.47 OPT=6.28 ATT=1.57 → **CCR** | CCR=7.07 OPT=6.28 ATT=1.57 → **CCR** | CCR=6.27 OPT=6.53 ATT=1.57 → **OPT** | CCR=5.90 OPT=6.53 ATT=1.57 → **OPT** |

CCR gana en **13/20** celdas (típicamente V alto / kappa bajo).


## RQ3 — Robustez ante degradación (pool=clean, V=5, k=0.2)

attestation_only confía en cards firmadas válidas; el agente honesto pero **degradado** está firmado y la card está obsoleta → lo elige y falla.

| policy | success@1 | net_utility | p95_ms |
|---|---|---|---|
| ccr | 0.9861 | 3.0528 | 984 |
| card_only | 0.5556 | 1.7778 | 638 |
| attestation_only | 0.5556 | 1.7778 | 638 |
| optimistic_fallback | 1 | 3.5139 | 809 |


## RQ4 — Defección canary-aware y mitigación (pool=mixed, V=5, k=0.2)

El defector pasa el canary y falla la tarea real **solo si puede distinguir la prueba**. Hacer el canary indistinguible (I) lo neutraliza vs. distinguible (D).

| configuración CCR | defection_rate | incident_rate | success@1 |
|---|---|---|---|
| canary distinguible (D) — naive | 0.8542 | 1 | 0.8125 |
| canary indistinguible (I) — mitigación | 0.1111 | 0.1389 | 0.9444 |


_Nota: números deterministas; el despliegue en Azure los reproduce sobre A2A real._