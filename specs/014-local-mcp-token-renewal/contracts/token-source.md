# Contrato de credencial local

Auth desactivada: sin bearer. Archivo configurado: releer en cada petición; error implica ausencia de credencial, sin fallback. Sin archivo: token env estático anterior. Token explícito de perfil sin archivo explícito: anular herencia de archivo. Archivo explícito: fuente elegida por operador.

Publicación: issuer/sub/aud/scope estables, iat/exp nuevos, write atómico 0600. Renovador no reinicia hijos ni reenvía requests. Error de publicación conserva archivo anterior y reintenta; una credencial vencida siempre se rechaza por API. Un shutdown normal hace Event.set y join, luego cierra API/issuer.
