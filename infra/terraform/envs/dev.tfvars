# Ambiente dev: donde se prueba que un cambio de infraestructura o de código aplica y corre.
# Se despliega solo cuando el CI da verde (.github/workflows/deploy.yml).
#
#   terraform workspace select dev
#   terraform apply -var-file=envs/dev.tfvars

environment = "dev"

# El mínimo que acepta Glue para un job de Spark. Dev no procesa los 18 millones de filas de
# producción: alcanza con una fuente chica (fractura) para saber si el pipeline corre.
number_of_workers = 2

# Deshabilitados: dev existe para probar cambios a mano, no para quedar corriendo solo.
enable_schedule = false
