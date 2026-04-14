{
  description = "DimOS Arduino support — bridge binary + Arduino toolchain";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    dimos-lcm = {
      url = "github:dimensionalOS/dimos-lcm/main";
      flake = false;
    };
    # Patched LCM that builds cleanly on macOS (pkg-config + fdatasync
    # fixes).  On Linux this is identical to upstream pkgs.lcm.
    lcm-extended.url = "github:jeff-hykin/lcm_extended";
  };

  outputs = { self, nixpkgs, flake-utils, dimos-lcm, lcm-extended }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        # Single-output LCM built on top of lcm_extended.
        # We still collapse outputs to a single `out` so downstream
        # CMakeLists.txt doesn't have to juggle `lcm` vs `lcm-dev` paths.
        lcmFull = (lcm-extended.packages.${system}.lcm).overrideAttrs (old: {
          outputs = [ "out" ];
          postInstall = "";
        });

        # The generic serial↔LCM bridge
        arduino_bridge = pkgs.stdenv.mkDerivation {
          pname = "arduino_bridge";
          version = "0.1.0";
          src = ./.;

          nativeBuildInputs = [ pkgs.cmake pkgs.pkg-config ];
          buildInputs = [ lcmFull pkgs.glib ];

          cmakeFlags = [
            "-DDIMOS_LCM_DIR=${dimos-lcm}"
          ];

          # CMakeLists.txt is in cpp/ subdirectory
          cmakeDir = "../cpp";

          installPhase = ''
            mkdir -p $out/bin
            cp arduino_bridge $out/bin/
          '';
        };

      in {
        packages = {
          inherit arduino_bridge;
          default = arduino_bridge;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            arduino_bridge
            pkgs.arduino-cli
            pkgs.avrdude
            pkgs.picocom
            # qemu-system-avr for virtual-Arduino mode.  `pkgs.qemu` builds
            # all system targets including avr and works on darwin +
            # linux; it's ~400MB but cached via the public binary cache
            # on common platforms so first-time install is the only cost.
            pkgs.qemu
          ];
        };
      });
}
