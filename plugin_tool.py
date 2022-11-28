import argparse, re, os, shutil, requests, sys

parser = argparse.ArgumentParser(
    usage="Put this file in your root project directory and execute the commands.",
    description="A tool to help you with initializing and building a Flutter plugin with Rust."
)

parser.add_argument(
    "-i", "--init",
    action="store_true",
    help="Initialize the Flutter plugin project for development with Rust."
)

parser.add_argument(
    "-c", "--code-gen",
    action="store_true",
    help="Generates the FFI bridge using flutter_rust_bridge."
)

parser.add_argument(
    "-b", "--build",
    action="store_true",
    help="Builds the Rust code. This will have to be run on Linux, Windows, and macOS if you want to target all platforms."
)

parser.add_argument(
    "--ios-ssl",
    action="store",
    help="Used to fix the build for OpenSSL if the vendored feature is being used on aarch64-apple-ios-sim target. Please provide the path to the openssl include directory."
)

def init():
    print("Initializing your project...")

    # Add dependencies to pubspec.yaml.
    print("Adding dependencies to pubspec.yaml...")

    pubspec_text = open("./pubspec.yaml", "r").read()
    package_name = pubspec_text.split("name: ")[1].split("\n")[0].strip()
    pascal_case_package_name = package_name.lower().replace("_", " ").title().replace(" ", "")
    
    with open("./pubspec.yaml", "w") as pubspec:
        def add_dependency(dependency:str, version:str = "", dev:bool = False) -> str:
            key = ("dev_" if dev else "") + "dependencies"
            split:list[str] = re.split(rf"\s{key}:\s", pubspec_text)
            lines:list[str] = split[1].split("\n")
            
            for i in range(0, len(lines)):
                if lines[i].isspace() or len(lines[i]) == 0:
                    break

            lines.insert(i, f"  {dependency}: {version}")

            return split[0] + f"\n{key}:\n" + "\n".join(lines)

        if "ffi:" not in pubspec_text:
            pubspec_text = add_dependency("ffi", version="^2.0.1", dev=False)

        if "flutter_rust_bridge:" not in pubspec_text:
            pubspec_text = add_dependency("flutter_rust_bridge", version="^1.50.0", dev=False)
        
        if "ffigen:" not in pubspec_text:
            pubspec_text = add_dependency("ffigen", version="^6.1.2", dev=True)

        pubspec.write(pubspec_text)

    # Start the Rust project.
    print(f"Creating the Rust project with the name \"{package_name}\"...")
    
    os.makedirs("rust", exist_ok=True)
    path = f"./rust/{package_name}"
    os.system(f"cargo new {path} --lib")

    for item in os.listdir(path):
        shutil.move(f"{path}/{item}", f"./rust")

    os.rmdir(path)

    toml_text = open("./rust/Cargo.toml", "r").read()
    with open("./rust/Cargo.toml", "w") as toml:
        split = toml_text.split("\n\n")
        toml_text = split[0] + '\n\n[lib]\ncrate-type = ["staticlib", "cdylib"]\n\n' + "\n".join(split[1:])
        toml.write(toml_text)

    # Initialize the Flutter platform specific things.
    print("Initializing platform specific project files...\n")
    
    # Android
    gradle_text = open("./android/build.gradle", "r").read()
    if "main.jniLibs.srcDirs = ['src/main/jniLibs']" not in gradle_text:
        with open("./android/build.gradle", "w") as gradle:
            split = gradle_text.split("sourceSets {")
            split[1] = "\t\tmain.jniLibs.srcDirs = ['src/main/jniLibs']" + split[1]
            gradle.write(split[0] + "sourceSets {\n" + split[1])

    # Linux
    linux_cmake_text = open("./linux/CMakeLists.txt", "r").read()
    if f'set(CRATE_NAME, "{package_name}")' not in linux_cmake_text:
        with open("./linux/CMakeLists.txt", "w") as cmake:
            split = linux_cmake_text.split(f"set({package_name}_bundled_libraries")
            split[0] = split[0] + 'set(CRATE_NAME, "pkgname")\nset(CRATE_NAME ${CRATE_NAME} PARENT_SCOPE)\nadd_subdirectory(${CRATE_NAME})'.replace("pkgname", package_name)
            split[1] = split[1].replace('""', r'"$<TARGET_FILE:${CRATE_NAME}>"')
            linux_cmake_text = split[0] + f"\nset({package_name}_bundled_libraries" + split[1]
            cmake.write(linux_cmake_text)

    if not os.path.exists(f"./linux/{package_name}/CMakeLists.txt"):
        os.mkdir(f"./linux/{package_name}")
        with open(f"./linux/{package_name}/CMakeLists.txt", "w") as cmake:
            cmake.write(
                'add_library(${CRATE_NAME} SHARED IMPORTED GLOBAL)\nset_property(TARGET ${CRATE_NAME} PROPERTY IMPORTED_LOCATION "${CMAKE_CURRENT_SOURCE_DIR}/libpkgname.so")'
                .replace("pkgname", package_name)
            )

    # Windows
    windows_cmake_text = open("./windows/CMakeLists.txt", "r").read()
    if f'set(CRATE_NAME, "{package_name}")' not in windows_cmake_text:
        with open("./windows/CMakeLists.txt", "w") as cmake:
            split = windows_cmake_text.split(f"set({package_name}_bundled_libraries")
            split[1] = split[1].replace('""', r'"${CMAKE_CURRENT_SOURCE_DIR}/pkgname.dll"')
            windows_cmake_text = split[0] + f"\nset({package_name}_bundled_libraries" + split[1]
            cmake.write(windows_cmake_text)

    # macOS
    mac_podspec = open(f"./macos/{package_name}.podspec", "r").read()
    if "s.vendored_libraries" not in mac_podspec:
        with open(f"./macos/{package_name}.podspec", "w") as podspec:
            # Remove the end keyword ----------v
            mac_podspec = mac_podspec.strip()[:-3] + "  s.vendored_libraries = 'Libs/**/*'\nend"
            podspec.write(mac_podspec)

    # iOS
    ios_podspec = open(f"./ios/{package_name}.podspec", "r").read()
    if "s.vendored_frameworks" not in ios_podspec:
        with open(f"./ios/{package_name}.podspec", "w") as podspec:
            ios_podspec = ios_podspec.strip()[:-3] + "  s.vendored_frameworks = 'Frameworks/**/*.xcframework'\n  s.static_framework = true\nend"
            podspec.write(ios_podspec)

    # If this is a Swift project
    if os.path.exists(f"./ios/Classes/Swift{pascal_case_package_name}Plugin.swift"):
        swift_text = open(f"./ios/Classes/Swift{pascal_case_package_name}Plugin.swift", "r").read()
        
        if "dummy_method" not in swift_text:
            with open(f"./ios/Classes/Swift{pascal_case_package_name}Plugin.swift", "w") as swift:
                entry_point = "public static func register(with registrar: FlutterPluginRegistrar) {"
                split = swift_text.split(entry_point)
                split[1] = "\n\t\tdummy_method_to_enforce_bundling()" + split[1]
                swift_text = split[0] + entry_point + split[1]
                swift.write(swift_text)
    # Obj-C project
    else:
        objc_text = open(f"./ios/Classes/{pascal_case_package_name}Plugin.m", "r").read()

        if "dummy_method" not in objc_text:
            with open(f"./ios/Classes/{pascal_case_package_name}Plugin.m", "w") as objc:
                objc_text = '#import "../Classes/bridge_generated.h"' + objc_text
                entry_point = "+ (void)registerWithRegistrar:(NSObject<FlutterPluginRegistrar>*)registrar {"
                split = objc_text.split(entry_point)
                split[1] = "\n\tdummy_method_to_enforce_bundling();" + split[1]
                objc_text = split[0] + entry_point + split[1]
                objc.write(objc_text)


def code_gen():
    print("Generating code with flutter_rust_bridge...\n")

    os.system("cargo install flutter_rust_bridge_codegen")
    os.system('CPATH="$(clang -v 2>&1 | grep "Selected GCC installation" | rev | cut -d\' \' -f1 | rev)/include" \
        flutter_rust_bridge_codegen \
        --rust-input ./rust/src/lib.rs \
        --dart-output ./lib/src/bridge_generated.dart \
        --dart-decl-output ./lib/src/bridge_definitions.dart \
        --c-output ./ios/Classes/bridge_generated.h \
        --c-output ./macos/Classes/bridge_generated.h')

    # Fix the incorrect import in the generated file.
    # This happens because we are using lib.rs as the entry point.
    generated_text = open("./rust/src/bridge_generated.rs", "r").read()
    open("./rust/src/bridge_generated.rs", "w").write(generated_text.replace("use crate::lib::*;", "use crate::*;"))

    if "ffi.dart" not in os.listdir("./lib/src"):
        package_name = open("./rust/Cargo.toml", "r").read().split("name = \"")[1].split("\"")[0]
        pascal_case_package_name = package_name.lower().replace("_", " ").title().replace(" ", "")

        file = open("./lib/src/ffi.dart", "w")
        file.write(
            requests.get(r"https://raw.githubusercontent.com/Desdaemon/flutter_rust_bridge_template/main/lib/ffi.dart")
                .text
                .replace("native", package_name)
                .replace("Native", pascal_case_package_name)
        )


def build(openssl_path:str = None):
    print("Building Rust code...\n")

    package_name = open("./rust/Cargo.toml", "r").read().split("name = \"")[1].split("\"")[0]
    is_linux = sys.platform == "linux"
    is_windows = sys.platform == "win32"
    is_mac = sys.platform == "darwin"

    if is_linux or is_windows or is_mac:
        print("Building Android libraries...\n")

        os.system("rustup target add aarch64-linux-android armv7-linux-androideabi x86_64-linux-android i686-linux-android")
        os.system("cargo install cargo-ndk")

        if os.path.exists("../android/src/main/jniLibs"):
            os.removedirs("../android/src/main/jniLibs")

        os.system("cd rust && cargo ndk -t arm64-v8a -t armeabi-v7a -t x86 -t x86_64 -o ../android/src/main/jniLibs build --release && cd ..")

    if is_linux:
        print("Building Linux libraries...\n")

        os.system("rustup target add x86_64-unknown-linux-gnu")
        os.system("cd rust && cargo build --release --target x86_64-unknown-linux-gnu && cd ..")
        os.makedirs(f"./linux/{package_name}", exist_ok=True)

        if os.path.exists(f"./linux/{package_name}/lib{package_name}.so"):
            os.remove(f"./linux/{package_name}/lib{package_name}.so")

        shutil.move(f"./rust/target/x86_64-unknown-linux-gnu/release/lib{package_name}.so", f"./linux/{package_name}")

    if is_windows:
        print("Building Windows libraries...\n")

        os.system("rustup target add x86_64-pc-windows-msvc")
        os.system("cd rust && cargo build --release --target x86_64-pc-windows-msvc && cd ..")

        if os.path.exists(f"./windows/{package_name}.dll"):
            os.remove(f"./windows/{package_name}.dll")

        shutil.move(f"./rust/target/x86_64-pc-windows-msvc/release/{package_name}.dll", "./windows")

    if is_mac:
        print("Building macOS libraries...\n")

        # Build for macOS.
        os.system("rustup target add aarch64-apple-darwin x86_64-apple-darwin")
        os.system("cd rust")
        os.system("cargo build --release --target aarch64-apple-darwin")
        os.system("cargo build --release --target x86_64-apple-darwin")
        os.system(f'lipo "./target/aarch64-apple-darwin/release/lib{package_name}.dylib" "target/x86_64-apple-darwin/release/lib{package_name}.dylib" -output "lib{package_name}.dylib" -create')
        os.system("cd ..")

        if os.path.exists(f"./macos/Libs/lib{package_name}.dylib"):
            os.remove(f"./macos/Libs/lib{package_name}.dylib")

        shutil.move(f"./rust/lib{package_name}.dylib", "./macos/Libs")

        # Build for iOS
        print("Building iOS libraries...\n")

        os.system("rustup target add aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios")
        os.system("cd rust")
        os.system("cargo build --release --target aarch64-apple-ios")

        env_vars = f"OPENSSL_STATIC=1 OPENSSL_LIB_DIR=/usr/local/lib OPENSSL_INCLUDE_DIR={openssl_path} OPENSSL_NO_VENDOR=1 " if openssl_path is not None else ""
        os.system(f"{env_vars}cargo build --release --target aarch64-apple-ios-sim")

        os.system("cargo build --release --target x86_64-apple-ios")
        os.system(f'lipo "target/aarch64-apple-ios-sim/release/lib{package_name}.a" "target/x86_64-apple-ios/release/lib{package_name}.a" -output "lib{package_name}.a" -create')
        os.system(f"xcodebuild -create-xcframework -library ./target/aarch64-apple-ios/release/lib{package_name}.a -library ./lib{package_name}.a -output {package_name}.xcframework")
        os.remove(f"./{package_name}.a")
        os.system("cd ..")

        if os.path.exists(f"./ios/Frameworks/{package_name}.xcframework"):
            os.removedirs(f"./ios/Frameworks/{package_name}.xcframework")

        shutil.move(f"./rust/{package_name}.xcframework", "./ios/Frameworks")


if __name__ == "__main__":
    args = parser.parse_args()

    if args.init:
        init()

    if args.code_gen:
        code_gen()

    if args.build:
        build(args.ios_ssl)