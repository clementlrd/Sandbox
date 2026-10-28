# Uninstalling sandbox

From a checkout of the source project:

```bash
./uninstall.sh
```

The default uninstall removes `~/.local/bin/sandbox`, installed Python code, Docker build files, installer scripts and the `sandbox-py` helper installed by the project. It does **not** destroy your named sandboxes, repositories, Docker volumes, config, denylist, recipes, downloaded OpenCode or other host-managed tools, Docker images or build cache. It does not touch your original Git repositories.

If you want a **complete local-data removal**:

1. Import/review all important Git commits and export any OpenCode credentials/sessions or Python environments you want to keep.
2. List and explicitly destroy every named sandbox with the still-installed CLI:

   ```bash
   sandbox list
   sandbox destroy NAME --yes
   ```

   `destroy` deletes that sandbox's saved Git clone and its **recorded named volumes**, including Python venvs and application credentials. Make sure no sandbox containers are still running.
3. Check for unreferenced `sandbox-*` volumes manually with `docker volume ls --filter name=sandbox-`. The uninstaller never removes untracked or orphaned volumes and never calls `docker system prune`.
4. Remove the installation and personal data:

   ```bash
   ./uninstall.sh --purge-data
   # or, for noninteractive automation: ./uninstall.sh --purge-data --yes
   ```

   This refuses to purge while saved sandbox-instance directories remain. It removes `${XDG_CONFIG_HOME:-~/.config}/sandbox`, `${XDG_STATE_HOME:-~/.local/state}/sandbox` and `${XDG_DATA_HOME:-~/.local/share}/sandbox`, including host recipes, downloaded tools, and denylist.

5. Docker images and BuildKit caches are left intact intentionally. Inspect them (`docker system df -v`, `docker buildx du`) and remove any unwanted, **unreferenced** images manually; beware image-ID pinning and shared XPU layers.

The `--purge-data` action is irreversible and should be treated as a separate, deliberate decision from uninstalling the program.
