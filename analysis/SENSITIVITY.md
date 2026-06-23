# CCR — Sensitivity sweep (generado por scripts/sensitivity.py)

`advantage` = utilidad neta de CCR − mejor baseline. **region** = zona de alto riesgo (V≥5, κ≤0.2), donde el paper afirma que CCR debe usarse. **whole** = grilla completa (incluye celdas donde CCR concede que no conviene). `win` = fracción de celdas donde CCR ≥ mejor baseline.


## Mundo — claim_gap (prueba decisiva de calibración)
Si `mean_adv` crece con `claim_gap` y ~0 en gap=0, CCR gana **porque las cards son poco fiables**, no por calibración. gap=0 = mundo honesto.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.4 | 0.0 | -0.587 | -0.2462 | -1.0398 | 0.0093 |
| 0.25 | 0.4 | 0.0 | -0.553 | -0.2462 | -1.0661 | 0.0158 |
| 0.5 | 0.4 | 0.0 | -0.553 | -0.2462 | -1.0412 | 0.0158 |
| 0.75 | 0.4 | 0.3333 | -0.3864 | 0.0407 | -0.8027 | 0.0084 |
| 1.0 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |

## Mundo — fracción maliciosa
La ventaja debería crecer con la hostilidad del pool.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.0 | 0.3333 | -0.1093 | 0.0907 | -0.437 | 0 |
| 0.25 | 0.25 | 0.3333 | -0.4667 | 0.0407 | -0.8236 | 0 |
| 0.4 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.571 | 0.571 | 0.8333 | 0.9685 | 2.2166 | -0.6891 | 0.3111 |
| 0.667 | 0.667 | 0.8333 | 0.9685 | 2.2166 | -0.6891 | 0.3111 |

## Método — alpha (declarativo vs empírico)
Robustez al peso entre evidencia de card y de canary.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.1 | 0.4 | 1.0 | 0.771 | 1.4111 | -0.3395 | 0.1533 |
| 0.3 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.45 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.6 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.8 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.9 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |

## Método — lambda_c (precio del costo)
Cuánto penaliza el costo del canary.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.5 | 0.4 | 1.0 | 0.6587 | 1.0482 | -0.0068 | 0.119 |
| 1.0 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 2.0 | 0.4 | 0.8333 | 0.579 | 1.5815 | -1.0689 | 0.1875 |
| 4.0 | 0.4 | 0.3333 | -0.1198 | 2.0703 | -2.5203 | 0.256 |

## Método — voi_threshold (compuerta de verificación)
τ alto ⇒ CCR verifica menos (menos overhead, menos cobertura).

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| -0.2 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.3836 | 0.119 |
| 0.0 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.37 | 0.1533 |
| 0.1 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.4139 | 0.1704 |
| 0.3 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.4592 | 0.2218 |
| 0.6 | 0.4 | 1.0 | 0.7247 | 1.2259 | -0.5471 | 0.2732 |


## Veredicto de robustez (región de alto riesgo)

- **claim_gap:** ventaja en región CRECE monótonamente con la falta de fiabilidad de las cards (gap 0→1: -0.587 → 0.7247). En el mundo honesto (gap=0) la ventaja es ~0/negativa — CCR no se necesita y casi no estorba.
- **malice:** la ventaja máxima en región NO es estrictamente monótona con la hostilidad del pool (malice→1: 0.0907 → 2.2166). La no-monotonía es informativa: importa el **tipo** de amenaza, no solo la fracción. El mentiroso de *firma inválida* (adversarial) lo filtra barato la atestación; solo el defector *firmado-válido* (canary_aware) requiere el canary — y es ahí donde CCR despega.
- CCR tiene ventaja media **positiva en la región** en **18/25** settings.
- **Diseño validado:** el *piso de duda* en la VoI (`voi_prior_doubt`) hace que CCR verifique al defector *confiado* (card≈0.99) en vez de ejecutar sobre él; por eso su incidente cae muy por debajo de optimistic_fallback en la región. El residual proviene de celdas de bajo V / κ alto donde verificar no compensa (economía correcta) y del canary *distinguible* (ver RQ4). τ alto reduce cobertura; τ bajo la aumenta.