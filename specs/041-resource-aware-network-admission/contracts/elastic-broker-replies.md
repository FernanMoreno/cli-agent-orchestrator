# Respuestas del broker elástico

Identidad y lease son modelos strict/frozen con extras ignorados. worker_id admite un segmento ASCII alfanumérico/guion/underscore de hasta128caracteres, sin controles; se preserva worker-1 heredado y8hex real. target_host, working_directory y session_name son strings canónicos acotados. Se valida identidad antes del lease completo para retener un puntero seguro en reconcile; ningún otro dato parcial autoriza un envío. release_token no se almacena ni propaga.

DELETE exige status2xx y un ElasticWorkerRelease con booleanos exactos released=true/workload_present=false. Ausencia de cuerpo, otros tipos, workload presente, respuestas HTTPerróneas o404 genérico conservan reconcile. No se repite el intento durable y no se declaran liberados créditos de recursos. El productor revisado es examples/cao-clusters/kubernetes/eks/broker.py; HTTP/Kubernetes están simulados en la aceptación de este componente, PostgreSQL es real.

[Validación](../validation-profile-elastic-broker.json), [composición](../composition-profile-elastic-broker.md).
