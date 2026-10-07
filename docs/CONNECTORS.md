# Conectores y fuentes

`job_radar/connectors/registry.py` es la fuente de verdad para empresas,
parámetros, orden y flags. El registro actual contiene 70 conectores.

## Catálogo

| Implementación | Empresas |
| --- | --- |
| Greenhouse (`fetch_greenhouse`) | Typeform, N26, Stripe, Adyen, Block (incl. Afterpay), Chime, Nubank, Robinhood, SoFi, Coinbase, Datadog, Clarity AI, Fever, Cabify, Aircall, Auctane, Celonis, Taxbit, Ebury, Lynx, Monzo, Make, Awin, Blip Global, OneTrust, nCino, Affirm, Raisin |
| Ashby (`fetch_ashby`) | Pleo, Plaid, Qonto, Mollie, Capchase, Invopop, Airwallex, Checkout.com, Rain, Lovable, n8n, ClickHouse, Ashby, StackAI, Camunda, Supabase |
| Workday (`fetch_workday`) | Mastercard, BBVA, Santander, Amadeus, AVEVA |
| SmartRecruiters (`fetch_smartrecruiters`) | IFS, Wise, Grab / Grab Financial Group |
| SuccessFactors (`fetch_successfactors`) | SAP, Hexagon |
| Lever (`fetch_lever`) | Paytm |
| Deel Jobs (`fetch_deel_company`) | Klarna |
| BambooHR (`fetch_bamboohr`) | Flutterwave |
| Eightfold (`fetch_eightfold`) | PayPal |
| Teamtailor | Spendesk, Seedtag, Lingokids |
| Comeet | ThetaRay |
| iCIMS | Mambu |
| Sage People | Sage |
| Portales propios | Ant Group / Ant International, Deel, Revolut, CaixaBank Tech, Dassault Systèmes, Visma |

Los primeros nueve tipos se configuran mediante diccionarios y `_configured`.
Los doce conectores del bloque final de `CONNECTORS` llaman funciones de
`job_radar/connectors/custom.py`; algunos reutilizan internamente patrones del proveedor, pero no
comparten un registro genérico.

## Cuándo reutilizar y cuándo crear custom

Reutiliza un conector de `job_radar/connectors/ats.py` cuando el career site exponga el mismo
contrato y solo cambien slug, tenant, host o plantilla de URL. Añadir una
empresa suele requerir una entrada en el diccionario correspondiente y un test
de configuración.

Un conector custom solo se justifica cuando ningún conector reutilizable puede
obtener el listado y los detalles necesarios. Antes de crearlo, documenta por
qué el ATS no encaja. No uses scraping HTML si existe una API pública estable.

## Contrato de mapping

Cada fetcher debe devolver `list[Job]` con:

- `source`: proveedor estable, no el nombre de una ejecución;
- `source_job_id`: identificador estable dentro de `source`;
- `company`, `title`, `location`, `url` y `description` publicados;
- `salary_text` y `experience_text`, publicados por el ATS o extraídos del
  texto sin inventar datos.

Si el ATS pagina, el conector debe recorrer todas las páginas previstas. Si
ofrece listado y detalle por separado, evita descargar detalles para títulos
claramente irrelevantes cuando el conector ya sigue ese patrón. Conserva el
modelo y la semántica existentes al corregir una fuente.

## Proceso para añadir una empresa

1. **Identifica el proveedor.** Comprueba la URL pública y las peticiones del
   career site; no asumas el ATS por apariencia.
2. **Valida el endpoint desde terminal.** Registra status HTTP, content type,
   estructura, número de ofertas y campos requeridos por el fetcher.
3. **Confirma el slug oficial.** Un 200 con lista vacía no demuestra por sí
   solo que un slug sea correcto si el proveedor acepta identificadores
   inexistentes.
4. **Comprueba relevancia geográfica.** Revisa los campos de ubicación para
   Madrid, España, remoto España o regiones abiertas a Europa/EMEA.
5. **Reutiliza el fetcher.** Añade la configuración mínima a `job_radar/connectors/registry.py`.
6. **Prueba el mapping.** Los tests deben mockear red y verificar URL, campos e
   identidad. No hacen llamadas reales a Internet.
7. **Revisa lifecycle.** Un identificador inestable genera duplicados; un
   snapshot incompleto puede cerrar ofertas si snapshot protection no lo
   detecta.
8. **Ejecuta la suite completa.** Verifica también el incremento exacto de
   `CONNECTORS` cuando se añaden fuentes.

## HTTP y errores

`job_radar/connectors/common.py` centraliza `urllib` para JSON y texto con User-Agent,
timeouts y reintentos. Hace un máximo de tres intentos ante timeouts, fallos de
conexión y HTTP 408/429/500/502/503/504. Un 400, 401, 403 o 404 se propaga sin
reintento. Algunos portales requieren `curl_cffi`; su wrapper reutiliza la
misma política para errores transitorios.

No conviertas indisponibilidad en un snapshot vacío. Si un proveedor responde
con una estructura incompatible, el conector debe fallar de forma visible para
que `source_runs` registre el error y no se ejecuten cierres para esa fuente.

## `required` y `catch_all`

- Las fuentes creadas por `_configured` son requeridas y no usan `catch_all`.
  Errores HTTP/red y errores de datos esperados se registran y permiten seguir;
  una excepción inesperada detiene el pipeline.
- Los conectores custom actuales usan `catch_all=True`, por lo que cualquier
  `Exception` se registra por fuente y el pipeline continúa.
- Revolut es la única fuente `required=False`. Su fallo deja el run `partial`,
  pero no activa salida bloqueante ni el aviso parcial del correo.

Consulta la tabla completa de resultados en
[ARCHITECTURE.md](ARCHITECTURE.md#observabilidad-y-semántica-de-errores).

## Checklist de revisión

- [ ] Endpoint y slug verificados en vivo.
- [ ] Payload compatible y con ofertas reales.
- [ ] Identidad `(source, source_job_id)` estable.
- [ ] URL de oferta original y HTTPS.
- [ ] HTML convertido a texto cuando corresponde.
- [ ] Salario y experiencia preservan el fragmento publicado.
- [ ] Paginación/listado-detalle cubiertos.
- [ ] Tests de URL, mapping, errores relevantes y registro.
- [ ] Suite completa, compilación y `git diff --check` correctos.
