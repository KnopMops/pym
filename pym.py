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


class PythonManager:
    def __init__(self, project_dir: Path = None):
        self.project_dir = project_dir or Path.cwd()
        self.pym_json = self.project_dir / "pym.json"
        self.pym_lock = self.project_dir / "pym.lock"
        self.python_version = self.project_dir / ".python-version"
        self.session = None
        self.cache_dir = Path.home() / ".fpk_cache"
        self.cache_dir.mkdir(exist_ok=True)

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args):
        await self.session.close()

    def init_project(self, name: str = None, python_version: str = None):
        if not name:
            name = self.project_dir.name

        if not python_version:
            python_version = f"{sys.version_info.major}.{sys.version_info.minor}"

        pym_data = {
            "name": name,
            "version": "1.0.0",
            "description": "",
            "dependencies": {},
            "devDependencies": {}
        }

        with open(self.pym_json, "w") as f:
            json.dump(pym_data, f, indent=2)

        with open(self.python_version, "w") as f:
            f.write(python_version)

        lock_data = {
            "lockfile_version": 1,
            "python_version": python_version,
            "dependencies": {},
            "hash": hashlib.sha256(b"").hexdigest()
        }

        with open(self.pym_lock, "w") as f:
            json.dump(lock_data, f, indent=2)

        print(f"✅ Инициализирован проект {name} с Python {python_version}")
        print(f"📁 Созданы файлы: pym.json, .python-version, pym.lock")

    async def add(self, packages: List[str], dev: bool = False):
        if not self.pym_json.exists():
            print("❌ Проект не инициализирован. Сначала выполните: pym init")
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

        print(f"📦 Добавлены зависимости: {', '.join(packages)}")
        await self.install()

    async def install(self):
        if not self.pym_json.exists():
            print("❌ Проект не инициализирован. Сначала выполните: pym init")
            return

        with open(self.pym_json, "r") as f:
            pym_data = json.load(f)

        all_dependencies = {
            **pym_data.get("dependencies", {}), **pym_data.get("devDependencies", {})}

        if not all_dependencies:
            print("ℹ️  Нет зависимостей для установки")
            return

        print(f"🔧 Устанавливаю {len(all_dependencies)} зависимостей...")

        tasks = []
        for package_name, package_spec in all_dependencies.items():
            if package_spec.startswith("git+"):
                tasks.append(self.install_from_github(package_spec))
            else:
                tasks.append(self.install_from_pypi(
                    package_name, package_spec))

        await asyncio.gather(*tasks)
        await self.update_lock_file(all_dependencies)

    async def install_from_pypi(self, package_name: str, version_spec: str):
        try:
            if version_spec == "latest":
                process = await asyncio.create_subprocess_exec(
                    "pip", "install", package_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
            else:
                process = await asyncio.create_subprocess_exec(
                    "pip", "install", f"{package_name}{version_spec}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                print(f"✅ Установлен: {package_name}")
            else:
                print(f"❌ Ошибка установки {package_name}: {stderr.decode()}")

        except Exception as e:
            print(f"❌ Ошибка при установке {package_name}: {e}")

    async def install_from_github(self, package_spec: str):
        try:
            url = package_spec[4:]

            process = await asyncio.create_subprocess_exec(
                "pip", "install", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                print(f"✅ Установлен из GitHub: {url}")
            else:
                print(f"❌ Ошибка установки из GitHub {url}: {stderr.decode()}")

        except Exception as e:
            print(f"❌ Ошибка при установке из GitHub {package_spec}: {e}")

    async def update_lock_file(self, dependencies: Dict[str, str]):
        try:
            process = await asyncio.create_subprocess_exec(
                "pip", "freeze",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                print(
                    f"❌ Ошибка получения установленных пакетов: {stderr.decode()}")
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

            print("🔒 Lock файл обновлен")

        except Exception as e:
            print(f"❌ Ошибка обновления lock файла: {e}")

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

    def create_venv(self):
        venv_dir = self.project_dir / ".venv"

        if venv_dir.exists():
            print("ℹ️  Виртуальное окружение уже существует")
        else:
            python_exe = sys.executable
            process = subprocess.run(
                [python_exe, "-m", "venv", str(venv_dir)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            if process.returncode == 0:
                print(f"✅ Создано виртуальное окружение: {venv_dir}")
            else:
                print(
                    f"❌ Ошибка создания виртуального окружения: {process.stderr.decode()}")
                return

        if sys.platform == "win32":
            activate_script = venv_dir / "Scripts" / "activate.bat"
            if activate_script.exists():
                print("🔄 Активирую виртуальное окружение...")
                activate_path = str(activate_script.resolve())
                subprocess.Popen(
                    f'cmd /k "{activate_path}"', shell=True, cwd=str(self.project_dir))
            else:
                print(f"⚠️  Скрипт активации не найден: {activate_script}")


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
