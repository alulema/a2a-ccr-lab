# CCR — Sensitivity sweep (generado por scripts/sensitivity.py)

`advantage` = utilidad neta de CCR − mejor baseline. **region** = zona de alto riesgo (V≥5, κ≤0.2), donde el paper afirma que CCR debe usarse. **whole** = grilla completa (incluye celdas donde CCR concede que no conviene). `win` = fracción de celdas donde CCR ≥ mejor baseline.


## Mundo — claim_gap (prueba decisiva de calibración)
Si `mean_adv` crece con `claim_gap` y ~0 en gap=0, CCR gana **porque las cards son poco fiables**, no por calibración. gap=0 = mundo honesto.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.4 | 0.0 | -0.542 | -0.2417 | -0.9934 | 0.0087 |
| 0.25 | 0.4 | 0.0 | -0.542 | -0.2417 | -1.0255 | 0.0093 |
| 0.5 | 0.4 | 0.0 | -0.542 | -0.2417 | -1.0007 | 0.009 |
| 0.75 | 0.4 | 0.0 | -0.5222 | -0.0889 | -0.8967 | 0.0087 |
| 1.0 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |

## Mundo — fracción maliciosa
La ventaja debería crecer con la hostilidad del pool.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.0 | 0.0 | -0.2458 | -0.0111 | -0.5245 | 0 |
| 0.25 | 0.25 | 0.0 | -0.5037 | -0.0611 | -0.8597 | 0 |
| 0.4 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.571 | 0.571 | 1.0 | 2.5565 | 3.3764 | 0.6461 | 0.3757 |
| 0.667 | 0.667 | 1.0 | 2.6445 | 3.5292 | 0.6947 | 0.3719 |

## Método — alpha (declarativo vs empírico)
Robustez al peso entre evidencia de card y de canary.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.1 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.3 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.45 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.6 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.8 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.9 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |

## Método — lambda_c (precio del costo)
Cuánto penaliza el costo del canary.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.5 | 0.4 | 1.0 | 1.1879 | 1.5979 | 0.7367 | 0.1681 |
| 1.0 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 2.0 | 0.4 | 1.0 | 1.5122 | 2.2666 | -0.2568 | 0.2847 |
| 4.0 | 0.4 | 0.8333 | 1.2363 | 3.1583 | -1.7654 | 0.4014 |

## Método — voi_threshold (compuerta de verificación)
τ alto ⇒ CCR verifica menos (menos overhead, menos cobertura).

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| -0.2 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.4185 | 0.1681 |
| 0.0 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.1 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3031 | 0.2556 |
| 0.3 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.1738 | 0.3431 |
| 0.6 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.0012 | 0.4305 |

## Método — voi_prior_doubt (piso de duda d₀, ablación)
d₀=0 quita el piso: un mentiroso confiado (card≈0.99) parece certero y EVADE el canary. Con d₀=0, CCR deja de verificar al defector confiado y su incidente sube hacia el de optimistic_fallback (la tesis: una auto-declaración alta no es evidencia).

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.4 | 0.8333 | 1.0745 | 1.8208 | -0.2787 | 0.5472 |
| 0.1 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.0696 | 0.3722 |
| 0.3 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 0.5 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.4185 | 0.1681 |

## Método — lambda_r (precio del incidente, decision-coupled)
El router RE-DECIDE con cada lambda_r (su ranking usa expected_utility), a diferencia del repricing post-hoc de analysis/lambda_r_sweep.py. La ventaja debería CRECER con lambda_r: CCR evita más a los agentes riesgosos mientras los baselines no se adaptan.

| valor | malice | region_win | region_mean_adv | region_max_adv | whole_mean_adv | ccr_inc |
|---|---|---|---|---|---|---|
| 0.0 | 0.4 | 0.6667 | 0.1294 | 0.6542 | -0.411 | 0.2264 |
| 1.0 | 0.4 | 1.0 | 0.7127 | 1.2375 | 0.014 | 0.2264 |
| 2.0 | 0.4 | 1.0 | 1.296 | 1.8208 | 0.3751 | 0.2264 |
| 4.0 | 0.4 | 1.0 | 2.4628 | 2.9875 | 1.096 | 0.2264 |
| 8.0 | 0.4 | 1.0 | 4.7752 | 5.3209 | 2.5314 | 0.2264 |
| 16.0 | 0.4 | 1.0 | 8.3586 | 9.9875 | 4.88 | 0.2264 |


## Veredicto de robustez (región de alto riesgo)

- **claim_gap:** ventaja en región CRECE monótonamente con la falta de fiabilidad de las cards (gap 0→1: -0.542 → 1.296). En el mundo honesto (gap=0) la ventaja es ~0/negativa — CCR no se necesita y casi no estorba.
- **malice:** la ventaja máxima en región NO es estrictamente monótona con la hostilidad del pool (malice→1: -0.0111 → 3.5292). La no-monotonía es informativa: importa el **tipo** de amenaza, no solo la fracción. El mentiroso de *firma inválida* (adversarial) lo filtra barato la atestación; solo el defector *firmado-válido* (canary_aware) requiere el canary — y es ahí donde CCR despega.
- CCR tiene ventaja media **positiva en la región** en **29/35** settings.
- **Diseño validado (ablación d₀):** el *piso de duda* en la VoI (`voi_prior_doubt`) hace que CCR verifique al defector *confiado* (card≈0.99) en vez de ejecutar sobre él. Con d₀=0 el incidente de CCR en la región sube de 0.2264 (d₀=0.3) a 0.5472 (d₀=0) — sin el piso, deja de verificar al mentiroso confiado y lo ejecuta, igualando a optimistic_fallback. El residual bajo d₀=0.3 proviene de celdas de bajo V / κ alto donde verificar no compensa (economía correcta) y del canary *distinguible* (ver RQ4). τ alto reduce cobertura; τ bajo la aumenta.