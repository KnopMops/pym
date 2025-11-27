import argparse
import asyncio
import aiohttp
import os
import subprocess
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import sys
import shutil


class PythonManager:
    def __init__(self, project_dir: Path = None):
        self.project_dir = project_dir or Path.cwd()
        self.pym_json = self.project_dir / "pym.json"
        self.pym_lock = self.project_dir / "pym.lock"
        self.python_version = self.project_dir / ".python-version"
        self.venv_dir = self.project_dir / ".venv"
        self.session = None
        self.cache_dir = Path.home() / ".fpk_cache"
        self.cache_dir.mkdir(exist_ok=True)
        self._creation_flags = 0
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            self._creation_flags = subprocess.CREATE_NO_WINDOW
        self._run_kwargs = {
            "creationflags": self._creation_flags} if self._creation_flags else {}
        self._async_kwargs = {
            "creationflags": self._creation_flags} if self._creation_flags else {}

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    def _log(self, message: str):
        print(message, flush=True)

    def _detect_python_command(self) -> str:
        env_override = os.environ.get("PYM_PYTHON")
        candidates = []
        if env_override:
            candidates.append(env_override)

        exe = Path(sys.executable)
        if exe.name.lower().startswith("python"):
            candidates.append(str(exe))

        candidates.extend(["python", "py", "python3"])

        seen = set()
        for cmd in candidates:
            if not cmd or cmd in seen:
                continue
            seen.add(cmd)
            resolved = shutil.which(cmd) if len(Path(cmd).parts) == 1 else cmd
            if not resolved:
                continue
            try:
                result = subprocess.run(
                    [resolved, "--version"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    **self._run_kwargs,
                )
                if result.returncode == 0:
                    return resolved
            except FileNotFoundError:
                continue

        raise RuntimeError(
            "Не удалось найти установленный Python. "
            "Установите Python или укажите путь в переменной окружения PYM_PYTHON."
        )

    def ensure_venv(self) -> bool:
        if self.venv_dir.exists():
            return True

        self._log("ℹ️  Виртуальное окружение не найдено. Создаю .venv...")
        return self.create_venv(activate=False)

    def init_project(self, name: str = None, python_version: str = None):
        if not name:
            name = self.project_dir.name

        if not python_version:
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        self._log(f"📝 Создаю pym.json в {self.pym_json}")
        pym_data = {
            "name": name,
            "version": "1.0.0",
            "description": "",
            "dependencies": {},
            "devDependencies": {}
        }

        with open(self.pym_json, "w") as f:
            json.dump(pym_data, f, indent=2)

        self._log(f"📝 Записываю .python-version ({python_version})")
        with open(self.python_version, "w") as f:
            f.write(python_version)

        self._log(f"🧾 Создаю lock файл {self.pym_lock}")
        lock_data = {
            "lockfile_version": 1,
            "python_version": python_version,
            "dependencies": {},
            "hash": hashlib.sha256(b"").hexdigest()
        }

        with open(self.pym_lock, "w") as f:
            json.dump(lock_data, f, indent=2)

        self._log(f"✅ Инициализирован проект {name} с Python {python_version}")
        self._log(f"📁 Созданы файлы: pym.json, .python-version, pym.lock")

    async def add(self, packages: List[str], dev: bool = False):
        if not self.pym_json.exists():
            self._log("❌ Проект не инициализирован. Сначала выполните: pym init")
            return

        with open(self.pym_json, "r") as f:
            pym_data = json.load(f)

        dependency_key = "devDependencies" if dev else "dependencies"

        for package in packages:
            if package.startswith("git+"):
                pym_data[dependency_key][self._extract_package_name(
                    package)] = package
            else:
                pym_data[dependency_key][package] = "latest"

        with open(self.pym_json, "w") as f:
            json.dump(pym_data, f, indent=2)

        self._log(f"📦 Добавлены зависимости: {', '.join(packages)}")
        await self.install()

    async def install(self):
        if not self.pym_json.exists():
            self._log("❌ Проект не инициализирован. Сначала выполните: pym init")
            return

        with open(self.pym_json, "r") as f:
            pym_data = json.load(f)

        all_dependencies = {
            **pym_data.get("dependencies", {}), **pym_data.get("devDependencies", {})}

        if not all_dependencies:
            self._log("ℹ️  Нет зависимостей для установки")
            return

        if not self.ensure_venv():
            self._log("❌ Не удалось создать виртуальное окружение")
            return

        self._log(f"🔧 Устанавливаю {len(all_dependencies)} зависимостей...")

        for package_name, package_spec in all_dependencies.items():
            if package_spec.startswith("git+"):
                await self.install_from_github(package_spec)
            else:
                await self.install_from_pypi(package_name, package_spec)

        await self.update_lock_file(all_dependencies)

    def _get_pip_command(self):
        if sys.platform == "win32":
            venv_pip = self.venv_dir / "Scripts" / "pip.exe"
        else:
            venv_pip = self.venv_dir / "bin" / "pip"

        if self.venv_dir.exists() and venv_pip.exists():
            return str(venv_pip)
        return "pip"

    async def install_from_pypi(self, package_name: str, version_spec: str):
        try:
            pip_cmd = self._get_pip_command()
            if version_spec == "latest":
                process = await asyncio.create_subprocess_exec(
                    pip_cmd, "install", package_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **self._async_kwargs,
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    pip_cmd, "install", f"{package_name}{version_spec}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **self._async_kwargs,
                )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                self._log(f"✅ Установлен: {package_name}")
            else:
                self._log(
                    f"❌ Ошибка установки {package_name}: {stderr.decode().strip()}")

        except Exception as e:
            self._log(f"❌ Ошибка при установке {package_name}: {e}")

    async def install_from_github(self, package_spec: str):
        try:
            url = package_spec[4:]
            pip_cmd = self._get_pip_command()

            process = await asyncio.create_subprocess_exec(
                pip_cmd, "install", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._async_kwargs,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                self._log(f"✅ Установлен из GitHub: {url}")
            else:
                self._log(
                    f"❌ Ошибка установки из GitHub {url}: {stderr.decode().strip()}")

        except Exception as e:
            self._log(f"❌ Ошибка при установке из GitHub {package_spec}: {e}")

    async def update_lock_file(self, dependencies: Dict[str, str]):
        try:
            pip_cmd = self._get_pip_command()
            process = await asyncio.create_subprocess_exec(
                pip_cmd, "freeze",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._async_kwargs,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                self._log(
                    f"❌ Ошибка получения установленных пакетов: {stderr.decode().strip()}")
                return

            installed_packages = {}
            for line in stdout.decode().splitlines():
                if "==" in line:
                    name, version = line.split("==", 1)
                    installed_packages[name.lower()] = {
                        "version": version,
                        "resolved": f"pypi:{version}"
                    }

            lock_data = {
                "lockfile_version": 1,
                "python_version": self.get_python_version(),
                "created": datetime.utcnow().isoformat() + "Z",
                "dependencies": installed_packages,
                "hash": self.calculate_dependencies_hash(dependencies)
            }

            with open(self.pym_lock, "w") as f:
                json.dump(lock_data, f, indent=2)

            self._log("🔒 Lock файл обновлен")

        except Exception as e:
            self._log(f"❌ Ошибка обновления lock файла: {e}")

    def get_python_version(self):
        if self.python_version.exists():
            with open(self.python_version, "r") as f:
                return f.read().strip()
        return f"{sys.version_info.major}.{sys.version_info.minor}"

    def calculate_dependencies_hash(self, dependencies: Dict[str, str]) -> str:
        deps_string = json.dumps(dependencies, sort_keys=True)
        return hashlib.sha256(deps_string.encode()).hexdigest()

    def _extract_package_name(self, github_url: str) -> str:
        url = github_url[4:]
        if url.endswith(".git"):
            url = url[:-4]
        return url.split("/")[-1]

    def create_venv(self, activate: bool = True) -> bool:
        if self.venv_dir.exists():
            self._log("ℹ️  Виртуальное окружение уже существует")
        else:
            try:
                python_cmd = self._detect_python_command()
            except RuntimeError as error:
                self._log(f"❌ {error}")
                return False

            self._log(
                f"🐍 Использую интерпретатор {python_cmd} для создания .venv")
            process = subprocess.run(
                [python_cmd, "-m", "venv", str(self.venv_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **self._run_kwargs,
            )

            if process.returncode == 0:
                self._log(f"✅ Создано виртуальное окружение: {self.venv_dir}")
            else:
                self._log(
                    f"❌ Ошибка создания виртуального окружения: {process.stderr.decode().strip()}")
                return False

        activate_hint = ".\\.venv\\Scripts\\activate" if sys.platform == "win32" else "source .venv/bin/activate"

        if activate and sys.platform == "win32":
            activate_script = self.venv_dir / "Scripts" / "activate.bat"
            if activate_script.exists():
                self._log(
                    "ℹ️  Для активации в текущем окне выполните команду ниже.")
            else:
                self._log(f"⚠️  Скрипт активации не найден: {activate_script}")

        self._log(
            f"👉 Чтобы активировать окружение вручную, выполните: {activate_hint}")
        return True


async def main():
    parser = argparse.ArgumentParser(description="Fast Python Package Manager")
    subparsers = parser.add_subparsers(
        dest="command", help="Доступные команды")

    init_parser = subparsers.add_parser(
        "init", help="Инициализировать новый проект")
    init_parser.add_argument("--name", help="Имя проекта")
    init_parser.add_argument("--python", help="Версия Python")

    subparsers.add_parser("install", help="Установить все зависимости")

    add_parser = subparsers.add_parser("add", help="Добавить зависимости")
    add_parser.add_argument("packages", nargs="+",
                            help="Имена пакетов или GitHub URLs")
    add_parser.add_argument("--dev", action="store_true",
                            help="Добавить в dev зависимости")

    subparsers.add_parser(
        "venv", help="Создать и активировать виртуальное окружение")

    args = parser.parse_args()

    async with PythonManager() as fpm:
        if args.command == "init":
            fpm.init_project(args.name, args.python)
        elif args.command == "install":
            await fpm.install()
        elif args.command == "add":
            await fpm.add(args.packages, args.dev)
        elif args.command == "venv":
            fpm.create_venv()
        else:
            parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
