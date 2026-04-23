# Política de Seguridad

## Alcance

Esta herramienta es una aplicación **local de auditoría ciudadana** que consume
exclusivamente la API pública de ONPE. No gestiona credenciales electorales,
no almacena datos personales de votantes y no tiene acceso de escritura a
ningún sistema electoral oficial.

## Reportar una vulnerabilidad

Si encuentras una vulnerabilidad de seguridad (p. ej. exposición de credenciales,
ejecución remota de código, path traversal en la API local), por favor:

1. **No abras un Issue público** con los detalles de la vulnerabilidad.
2. Envía un reporte privado a través de
   [GitHub Security Advisories](../../security/advisories/new) de este repositorio.
3. Incluye:
   - Descripción del problema
   - Pasos para reproducirlo
   - Impacto potencial estimado

Responderemos en un plazo máximo de **7 días hábiles**.

## Buenas prácticas para colaboradores

- **Nunca** incluyas credenciales de Google Cloud, API keys ni JSONs de cuenta de
  servicio en commits, Issues o Pull Requests.
- Usa `.env` (excluido por `.gitignore`) para todas las variables sensibles.
- El archivo `.env.example` solo debe contener valores de ejemplo, nunca reales.
- Los PDFs de actas descargados son datos electorales de terceros; no los publiques.
