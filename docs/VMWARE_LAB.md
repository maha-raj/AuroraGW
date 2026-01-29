# VMware Lab Guide (Complete Test Environment)

This guide builds a repeatable VMware environment to test AuroraGW before deploying on your real hardware.

## Recommended VMware networks

Create 4 VMware virtual networks (Workstation examples shown):
- **VMnet2** = `WAN-LAB` (Host-only, no DHCP)
- **VMnet3** = `LAN` (Host-only, no DHCP)
- **VMnet4** = `OPT1` (Host-only, no DHCP)
- **VMnet8** = `NAT` (VMware NAT network, provides Internet)

## How many VMs?

Minimum to test routing/NAT + UI:
- **2 VMs**
  1. `auroragw` (WAN + LAN + OPT1 NICs)
  2. `isp-sim` (WAN-LAB + NAT NICs)

Recommended to validate segmentation + discovery + gaming:
- **4 VMs**
  1. `auroragw` (3 NICs)
  2. `isp-sim` (2 NICs)
  3. `lan-client` (1 NIC on LAN)
  4. `opt1-client` (1 NIC on OPT1)

Optional to test PPPoE takeover end-to-end:
- **5 VMs** (add `pppoe-server` on WAN-LAB + NAT)

## VM NIC layouts

### 1) `auroragw` VM (Ubuntu Server)
- NIC1 → `WAN-LAB` (VMnet2)
- NIC2 → `LAN` (VMnet3)
- NIC3 → `OPT1` (VMnet4)

### 2) `isp-sim` VM (Ubuntu Server)
- NIC1 → `WAN-LAB` (VMnet2)
- NIC2 → `NAT` (VMnet8)

### 3) `lan-client` VM
- NIC1 → `LAN` (VMnet3)

### 4) `opt1-client` VM
- NIC1 → `OPT1` (VMnet4)

## Step-by-step: ISP simulator (DHCP + NAT for bootstrap)

On `isp-sim` VM:
```bash
cd AuroraGW/lab/vmware
sudo ./isp-sim-setup.sh
```

If you see `Cannot find device "eth0"` (or similar), your VM’s interface names are probably `ens33`/`ens34` (common on VMware).
Check your NIC names:
```bash
ip -br link
ip route show default
```
Then run with explicit interfaces:
```bash
WAN_IF=<wanlab-nic> UPLINK_IF=<nat-nic> sudo ./isp-sim-setup.sh
```

Result:
- WAN-LAB gateway: `10.0.2.1/24`
- DHCP on WAN-LAB gives AuroraGW WAN an IP like `10.0.2.x`
- NATs traffic out via the VM’s NAT uplink

Reboot note:
- `isp-sim-setup.sh` installs a `auroragw-isp-sim.service` so DHCP+NAT comes back after reboot.
- Check with:
```bash
systemctl status auroragw-isp-sim.service --no-pager
systemctl status dnsmasq --no-pager
ip -br addr
```

## Step-by-step: AuroraGW install (bootstrap mode)

On `auroragw` VM:
```bash
git clone https://github.com/maha-raj/AuroraGW.git
cd AuroraGW
sudo ./install/auroragw-install.sh
```

During install:
- Select the **WAN/LAN/OPT1 interfaces** from the detected NIC list (the config still uses MAC binding for portability).
- Set WAN mode = `dhcp`
- Keep LAN = `192.168.101.1/24`
- Keep OPT1 = `192.168.102.1/24`
- Enable discovery relay + UPnP if you want to validate home device flows

After install, from `lan-client`:
- Web UI: `https://192.168.101.1:8443/`

## Client VM setup scripts (inside the client VMs)

These scripts configure a client NIC with a static IP and install common tools.

### LAN client
```bash
cd AuroraGW/lab/vmware
sudo ./client-setup.sh --role lan
```

### OPT1 client
```bash
cd AuroraGW/lab/vmware
sudo ./client-setup.sh --role opt1
```

## Validation scripts

Run from the **LAN client**:
```bash
cd AuroraGW/lab/vmware
sudo ./lan-validate.sh --router 192.168.101.1
```

Run from the **OPT1 client**:
```bash
cd AuroraGW/lab/vmware
sudo ./opt1-validate.sh --router 192.168.102.1
```

## PPPoE testing (optional)

AuroraGW supports PPPoE mode, but the lab needs a PPPoE server.
If you want that workflow, add a dedicated `pppoe-server` VM and use:
- `lab/vmware/pppoe-server-setup.sh` (provided)

Then set AuroraGW `wan.mode: pppoe`, apply, confirm, and verify `pppoe0` comes up.
