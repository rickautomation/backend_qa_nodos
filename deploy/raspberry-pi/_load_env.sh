# Carga variables desde .env en la raíz del repo (no commitear .env).
_load_env() {
  local root="$1"
  local env_file="$root/.env"
  if [[ ! -f "$env_file" ]]; then
    return 0
  fi
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}

_setup_remote_cmds() {
  local ssh_password_opts=(
    -o StrictHostKeyChecking=accept-new
    -o PreferredAuthentications=password
    -o PubkeyAuthentication=no
    -o KbdInteractiveAuthentication=no
  )

  if [[ -n "${PASSWORD_PI:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    export SSHPASS="$PASSWORD_PI"
    local control_dir="${GREENBOX_SSH_CONTROL_DIR:-$HOME/.cache/greenbox-ssh}"
    mkdir -p "$control_dir"
    local control_path="$control_dir/%r@%h:%p"
    SSH_WRAP=(
      sshpass -e ssh
      "${ssh_password_opts[@]}"
      -o ControlMaster=auto
      -o "ControlPath=$control_path"
      -o ControlPersist=120
    )
    RSYNC_RSH="sshpass -e ssh ${ssh_password_opts[*]} -o ControlMaster=auto -o ControlPath=$control_path -o ControlPersist=120"
    USE_SUDO_S=1
    return 0
  fi

  if ssh -o BatchMode=yes -o ConnectTimeout=5 "$TARGET" true 2>/dev/null; then
    SSH_WRAP=(ssh)
    RSYNC_RSH="ssh"
    USE_SUDO_S=0
    return 0
  fi

  if [[ -n "${PASSWORD_PI:-}" ]]; then
    echo "ERROR: PASSWORD_PI está en .env pero falta sshpass."
    echo "Instalá con: brew install hudochenkov/sshpass/sshpass"
    exit 1
  fi

  echo "ERROR: no se pudo conectar a $TARGET."
  echo "Agregá PASSWORD_PI en .env o configurá una clave SSH (ssh-copy-id)."
  exit 1
}

_remote() {
  "${SSH_WRAP[@]}" "$TARGET" "$@"
}

_sudo_remote() {
  local cmd="$1"
  if [[ "${USE_SUDO_S:-0}" == "1" ]]; then
    printf '%s\n' "$PASSWORD_PI" | "${SSH_WRAP[@]}" "$TARGET" "sudo -S $cmd"
  else
    _remote "sudo $cmd"
  fi
}
