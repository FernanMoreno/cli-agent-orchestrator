# Research: spec007

## Ejecutable y perfil

**Decision**: fijar ruta absoluta y PATH para probe y launch; validar perfil nativo seleccionado antes de launch.
**Rationale**: diagnosis.md acredita que shell login elimina /opt/cao/host-tools/opencode. opencode_cli.py vuelve a resolver nombre tras detectar versión. install_service.py materializa perfiles con to_opencode_agent_id.
**Alternatives considered**: enlaces globales y cambio de shell requieren modificar entorno del propietario; instalar perfil automáticamente sobrescribe decisiones. Se rechazan.

## Estado de turno

**Decision**: tabla aditiva de recuperación por generación, separada de hijos nativos.
**Rationale**: recibo tiene CHECK prepared/sent/result_verified; NativeChild puede no existir en supervisor. Mantener ambos contratos y CAS de resultado.
**Alternatives considered**: liberar recibo o asumir éxito por estado visual duplica trabajo o inventa evidencia. Ampliar fases del recibo exige reconstrucción SQLite y afecta restauración de todos los proveedores; no necesario.

## Consulta y recuperación

**Decision**: proyección tipada compartida; verify solo observa; cancel exige generación y parada confirmada antes de permitir entrada nueva.
**Rationale**: ausencia esperada de recibo no es OutputExtractionError interno. C-c aislado no acredita parada ni libera recibo. FULL sigue siendo transcripción.
**Alternatives considered**: devolver todo como 500 pierde semántica; devolver transcripción como éxito vulnera evidencia; eliminar terminal pierde continuidad e identidad del supervisor.

## Pruebas y conocimiento

**Decision**: regresiones deterministas primero, SQLite real para CAS/reinicio y tmux real para lifecycle. Prueba integrada aislada de los tres proveedores al final.
**Rationale**: mocks no prueban locks ni shell efectiva. Spec006 ya tiene evidencia real, pero no acredita las correcciones nuevas.
**Alternatives considered**: duplicar runner completo de spec006 aumenta alcance. No persistir fuente/grafo en vault: artefactos de feature ya son durables y no hay nueva conclusión transversal antes de validar.

No quedan incógnitas de diseño. La ejecución real depende de disponibilidad de herramientas y credenciales existentes; una ausencia se registrará como caso no ejecutado.
