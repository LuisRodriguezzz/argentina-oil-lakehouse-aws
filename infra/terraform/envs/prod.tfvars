# Ambiente prod: el que tiene los datos completos y los números que cita el README raíz.
# Se despliega desde `main` y con aprobación manual (GitHub Environment `prod`).
#
#   terraform workspace select prod
#   terraform apply -var-file=envs/prod.tfvars

environment = "prod"

# Cuatro workers y no dos: el CSV anual de producción son millones de filas y con dos G.1X
# la carga de bronze se va del timeout de 60 minutos del job.
number_of_workers = 4

# También deshabilitados, y a propósito: el costo en reposo del proyecto tiene que seguir
# siendo cero (ADR 0001). Poner `true` acá es la decisión explícita de dejar el pipeline
# corriendo solo todos los meses; hasta entonces las corridas se disparan a mano.
enable_schedule = false
