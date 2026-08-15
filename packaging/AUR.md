# Publishing to the AUR

The AUR is the cheapest real distribution channel Voltaic has: Arch users
install with `yay -S voltaic` and get updates like any other package, and
nothing needs hosting.

Everything that can be prepared in advance is in this directory. What cannot
is the account: the AUR authenticates by SSH key, and only the maintainer can
push.

## One-time setup

1. Create an account at <https://aur.archlinux.org/register>.
2. Add your SSH public key under **My Account → SSH Public Key**.
3. Check it works:

   ```sh
   ssh aur@aur.archlinux.org help
   ```

## First submission

```sh
git clone ssh://aur@aur.archlinux.org/voltaic.git aur-voltaic
cd aur-voltaic

cp /path/to/voltaic/packaging/PKGBUILD .
cp /path/to/voltaic/packaging/.SRCINFO .

git add PKGBUILD .SRCINFO
git commit -m "Initial import: voltaic 1.3.0"
git push
```

The repository must contain `PKGBUILD` and `.SRCINFO` at its root — not the
`packaging/` prefix this project uses.

## Updating for a new release

**Order matters: tag first, then bump the PKGBUILD.** A PKGBUILD names a
tarball that only exists once the tag is pushed, so bumping `pkgver` ahead
of the tag points it at a 404. CI knows this and skips the download while
the tag is missing, but the package itself is not buildable until it exists.

`.SRCINFO` is generated, never edited by hand, and the AUR rejects a push
where it disagrees with the PKGBUILD.

```sh
# In this repository, after tagging vX.Y.Z:
sha256sum <(curl -fsSL https://github.com/jcfs/voltaic/archive/refs/tags/vX.Y.Z.tar.gz)
# update pkgver= and sha256sums= in packaging/PKGBUILD, then:
makepkg --printsrcinfo > packaging/.SRCINFO

# Then in the AUR clone:
cp /path/to/voltaic/packaging/{PKGBUILD,.SRCINFO} .
git commit -am "voltaic X.Y.Z"
git push
```

## Before pushing

CI builds the PKGBUILD on every push, so it should already be sound. To check
by hand on a non-Arch machine:

```sh
docker run --rm -v "$PWD":/src:ro archlinux:latest sh -c '
  pacman -Sy --noconfirm --needed base-devel > /dev/null
  useradd -m builder && cp /src/packaging/PKGBUILD /home/builder/
  chown -R builder:builder /home/builder
  su builder -c "cd ~ && makepkg --nodeps --noconfirm"'
```

This verifies the source checksum against the real release tarball, so a
PKGBUILD whose `pkgver` and `sha256sums` have drifted apart fails here rather
than for a user.

## Notes

- `pkgrel` goes back to `1` on every new `pkgver`, and increments only when
  the packaging changes without the upstream version changing.
- The package installs the udev rules to `/usr/lib/udev/rules.d/`. Arch does
  not run a trigger for those, so a user with the receiver already plugged in
  needs `sudo udevadm control --reload-rules && sudo udevadm trigger
  --subsystem-match=hidraw --action=change`, or simply to replug it. This is
  worth saying in the AUR comments on first import.
- Keep `depends` in step with what the code actually imports. `gtk3` is
  needed for the typelib even though nothing links against it directly.
