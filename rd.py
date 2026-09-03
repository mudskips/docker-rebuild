import os
import tarfile
import uuid
import stat
import click
import traceback
import linux


def _get_image_path(image_name, image_dir, image_suffix='tar'):
    return os.path.join(image_dir, os.extsep.join([image_name, image_suffix]))


def _get_container_path(container_id, container_dir, *subdir_names):
    return os.path.join(container_dir, container_id, *subdir_names)


def create_container_root(image_name, image_dir, container_id, container_dir):
    """Create a container root by extracting an image into a new directory"""
    image_path = _get_image_path(image_name, image_dir)
    container_root = _get_container_path(container_id, container_dir, 'rootfs')

    assert os.path.exists(image_path), "unable to locate image %s" % image_name

    if not os.path.exists(container_root):
        os.makedirs(container_root)

    with tarfile.open(image_path) as t:
        # Fun fact: tar files may contain *nix devices! *facepalm*
        members = [m for m in t.getmembers()
                   if m.type not in (tarfile.CHRTYPE, tarfile.BLKTYPE)]
        t.extractall(container_root, members=members)

    return container_root


@click.group()
def cli():
    pass


def contain(command, image_name, image_dir, container_id, container_dir):
    new_root = create_container_root(image_name, image_dir, container_id, container_dir)
    #create and isolate new namespace
    new_ns = linux.CLONE_NEWNS
    linux.unshare(new_ns)
    #privatize all mounts from '/'
    linux.mount(None, '/', None, linux.MS_PRIVATE | linux.MS_REC, None )
    # create mounts under new root
    linux.mount('proc', os.path.join(new_root, 'proc'), 'proc', 0, '')
    linux.mount('sysfs', os.path.join(new_root, "sys"), 'sysfs', 0, '')
    linux.mount('tmpfs', os.path.join(new_root, 'dev'), 'tmpfs', linux.MS_NOSUID | linux.MS_STRICTATIME, 'mode=755')
    devices = [('null', 1, 3), ('zero', 1, 5), ('random', 1, 8), ('urandom', 1, 9)]
    for device, major, minor in devices:
        os.mknod(os.path.join(new_root, 'dev', device), 0o666 | stat.S_IFCHR, os.makedev(major, minor))
    devpts_path = os.path.join(new_root, 'dev', 'pts')
    os.makedirs(devpts_path)
    linux.mount('devpts', devpts_path, 'devpts', 0, '')
    os.symlink('/proc/self/fd/0', new_root + '/dev/stdin')
    os.symlink('/proc/self/fd/1', new_root + '/dev/stdout')
    os.symlink('/proc/self/fd/2', new_root + '/dev/stderr')
    os.chroot(new_root)
    os.chdir("/")
    print(f'new_root created @{new_root}')
    env = dict(os.environ)
    os.execvpe(command[0], command, env)


@cli.command(context_settings=dict(ignore_unknown_options=True,))
@click.option('--image-name', '-i', help='Image name', default='ubuntu')
@click.option('--image-dir', '--idr', help='Images directory', default='/workshop/images')
@click.option('--container-dir','--cdr' , help='Containers directory', default='/workshop/containers')
@click.argument('Command', required=True, nargs=-1)
def run(image_name, image_dir, container_dir, command):
    container_id = str(uuid.uuid4())
    pid = os.fork()
    if pid == 0:
        # child
        try:
            contain(command, image_name, image_dir, container_id,
                    container_dir)
        except Exception:
            traceback.print_exc()
            os._exit(1)  # if something went wrong in contain()
    # parent
    # wait for the forked child, fetch the exit status
    _, status = os.waitpid(pid, 0)
    print('{} exited with status {}'.format(pid, status))


if __name__ == '__main__':
    cli()