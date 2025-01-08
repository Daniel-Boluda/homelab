# Guía para realizar resilvering en ZFS

El proceso de resilvering en ZFS es fundamental para restaurar la integridad de un pool después de reemplazar un disco defectuoso o añadir uno nuevo. Esta guía te llevará paso a paso para completar el proceso de manera segura.

## Requisitos previos

1. **Acceso al servidor**: Asegúrate de tener acceso al sistema con privilegios administrativos.
2. **Estado del pool**: Verifica el estado actual del pool con `zpool status`.
3. **Nuevo disco**: Asegúrate de que el nuevo disco está correctamente conectado y reconocido por el sistema operativo.

## Pasos para realizar resilvering

### 1. Identificar el pool y el disco afectado

Ejecuta el siguiente comando para verificar el estado del pool y los discos:

```bash
zpool status
```

Busca un estado de `DEGRADED` o `FAULTED` que indique un disco dañado o ausente.

### 2. Reemplazar el disco defectuoso

Si estás reemplazando un disco:

1. Identifica el dispositivo físico del disco defectuoso. Puedes usar herramientas como `lsblk` o `fdisk`.
2. Extrae físicamente el disco defectuoso y conecta el nuevo disco.

### 3. Informar a ZFS del nuevo disco

Usa el siguiente comando para reemplazar el disco en el pool:

```bash
zpool replace <nombre_del_pool> <disco_viejo> <disco_nuevo>
```

Por ejemplo:

```bash
zpool replace tank /dev/sda /dev/sdb
```

Si el disco antiguo ya no está presente, puedes especificar solo el disco nuevo:

```bash
zpool replace tank /dev/sdb
```

### 4. Verificar el inicio del resilvering

Ejecuta nuevamente `zpool status` para confirmar que el proceso de resilvering ha comenzado. Deberías ver una salida similar a:

```bash
scan: resilver in progress since <fecha>
```

### 5. Supervisar el progreso del resilvering

El proceso de resilvering puede llevar tiempo dependiendo del tamaño del pool y la cantidad de datos. Puedes monitorear el progreso con:

```bash
zpool status
```

### 6. Confirmar la finalización del resilvering

Una vez que el resilvering se complete, el estado del pool debería mostrarse como `ONLINE`.

```bash
zpool status
```

Salida esperada:

```bash
pool: tank
 state: ONLINE
...
```

### 7. Realizar una verificación final

Para asegurarte de que no haya errores residuales, ejecuta un scrub en el pool:

```bash
zpool scrub <nombre_del_pool>
```

Después de finalizar el scrub, verifica nuevamente el estado del pool.

## Notas adicionales

- Siempre asegúrate de tener respaldos actualizados antes de realizar operaciones críticas en el pool.
- Consulta la documentación oficial de ZFS para casos específicos o configuraciones avanzadas.

---
