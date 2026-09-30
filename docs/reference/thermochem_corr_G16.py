#!/usr/bin/python
import sys, string, math
######## Thermic calculations on Gaussian Frequency Job #########
#USAGE python thermochem_corr_G16 X.out [-t TEMP(K)] [-c cutoff frequency]

#Program takes a frequency output (from Gaussian) and computes thermochemical properties
#Program computes an output file with the same basename as the .out file, but with the extension defined below (name_extension)

#tested for g16 05.05.20 - compared with soft.f (T=298.16K, cutoff=100)

#This script is heavily "inspired" by GoodVibes:
#Luchini, G.; Alegre-Requena, J. V.; Funes-Ardoiz, I.; Paton, R. S. GoodVibes: Automated Thermochemistry for Heterogeneous Computational Chemistry Data. F1000Research, 2020, 9, 291 DOI: 10.12688/f1000research.22758.1
#Standard values are defined below
T=298.15  #Temperature
cutoff_freq=100 #Cutoff freq.
conc=1000.0 #Not ready for not standard - needs to include free_solv of GoodVibes.py
name_extension=".thch" #Name extension - THermoCHemistry
####CONSTANTS#############
R=1.987206613580357 #kcal/(K*mol)
gas_constant=8.31446261815323 #J/(K*mol)
boltzmann_constant=1.380649e-23 #J/K
avogadro_constant=6.02214076e23#1/mol
planck_constant=6.62607015e-34 #J*s
speed_of_light=2.99792458e10 #cm/s
J_to_H=4.184 * 627.5094740631 * 1000.0 #UNIT CONVERSION
H_to_kcal=627.5094740631 #UNIT CONVERSION
AMU_to_KG=1.6605390666050e-27 #UNIT CONVERSION

#Bolean variables stating whether temp. or cutoff. have been changed in the input
ch_temp=False
ch_cutoff=False
linear=False #Linear molecule has different reading of vib. freqs.
################################Interpreting arguments
if len(sys.argv)<2: #Missing input information
    sys.exit('Usage: python thermochem_corr_G16.py X.out [-t TEMP(K)] [-c cutoff freq.(cm^-1)]')
elif len(sys.argv)>3: #Some additions to temp. or cut.off have been stated in arg.
    #Checking whether temperature or cutoff comes up after "-t" and "-c" argument
    temp_next=False
    cutoff_next=False
    for i in range(2,len(sys.argv)):
        #Search for -t in arg.
        if (sys.argv[i]=='-t' or sys.argv[i]=='-T') and not ch_temp:
            if cutoff_next:
                sys.exit('Error. Seems like you have forgotten to enter the cutoff-freq acticated by "-c"')
            else:
                ch_temp=True
                temp_next=True
        elif (sys.argv[i]=='-c' or sys.argv[i]=='-C') and not ch_cutoff:
            if temp_next:
                sys.exit('Error. Seems like you have forgotten to enter the temperature activated by "-t"')
            else:
                ch_cutoff=True
                cutoff_next=True
        elif temp_next:
            temp_next=False
            if sys.argv[i].isdigit():
                T=float(sys.argv[i]) #Temp changed
            else: sys.exit('Tempearture must be a float or an integer')
        elif cutoff_next:
            cutoff_next=False
            if sys.argv[i].isdigit():
                cutoff_freq=float(sys.argv[i]) #Cutoff freq. changed
            else: sys.exit('Cutoff freq. must be a float or an integer')
        elif (sys.argv[i]=='-h' or sys.argv[i]=='-help' or sys.argv[i]=='--help'):
            sys.exit('Usage: python thermochem_corr_G16.py X.out [-t TEMP(K)] [-c cutoff freq.(cm^-1)]')
#############################################################
f1=open(sys.argv[1],'r')
###################Functions - modified from GoodVibes############
def calc_zpe(frequencies):
    #Calculates vibrational ZPE (rot. is neglected --small)
    #Output in Hartrees
    energy=[0.5*gas_constant*planck_constant*speed_of_light*freq/(boltzmann_constant*J_to_H) for freq in frequencies]
    return sum(energy)

def calc_vibrational_energy(frequencies,T):
    #Calculated vibrational energi - T dependent
    #Output in Hartrees
    factor=[(planck_constant*freq*speed_of_light)/(boltzmann_constant*T) for freq in frequencies]
    # Error occurs if T is too low when performing math.exp
    for entry in factor:
        if entry > math.log(sys.float_info.max):
            sys.exit("\nx  Warning! Temperature may be too low to calculate vibrational energy. Please adjust using the `-t` option and try again.\n")
    energy = [fac*gas_constant*T/J_to_H*(0.5+(1.0/(math.exp(fac)-1.0))) for fac in factor]
    return sum(energy)

def calc_rotational_energy(zpe,T,linear):
    #Calculated rotational energy
    #Output is in Hartrees
    if zpe == 0.0:
        energy=0.0
    elif linear:
        energy=gas_constant*T/J_to_H
    else:
        energy=1.5*gas_constant*T/J_to_H
    return energy

def calc_translational_energy(T):
    #Calculates translational energy of an ideal gas
    #Output is in Hartrees
    energy = 1.5*gas_constant*T/J_to_H
    return(energy)

def calc_translational_entropy(molecular_mass,T):
    #Calculated the translational entropy of an ideal gas
    #Output H/K
    lmda=((2.0*math.pi*molecular_mass*AMU_to_KG*boltzmann_constant*T)**0.5)/planck_constant
    conc=101325/(T*gas_constant) #Ideal gas law - pressure in Pa
    ndens=conc*avogadro_constant
    entropy=gas_constant*(2.5+math.log(lmda**3/ndens))/J_to_H
    return(entropy)

def calc_electronic_entropy(multiplicity):
    #Calculates electrnic entropy in H/K
    entropy=gas_constant*math.log(multiplicity)/J_to_H
    return entropy

def calc_rotational_entropy(zpe,linear,T,rotemp,symmno):
    #Calculated rotational entropy
    #Output in H/K
    if rotemp == [0.0,0.0,0.0] or zpe==0.0: #Monoatomic
        entropy=0.0
    else:
        if len(rotemp) == 1: #Diatomic or linear molecules
            linear=True #Goodvibes code
            qrot=T/rotemp[0]
        elif len(rotemp) == 2: #Possible gaussian problem with linear triatomic
            linear_not_defined = 2 #GoodVibes code
            entropy=0.0
        else:
            qrot=math.pi*T**3/(rotemp[0]*rotemp[1]*rotemp[2])
            qrot=qrot**0.5
        if linear:
            entropy= gas_constant*(math.log(qrot/symmno)+1)/J_to_H
        else:
            entropy=gas_constant*(math.log(qrot/symmno)+1.5)/J_to_H
    return entropy

def calc_rrho_entropy(frequency,T):
    #Entropic contribution H/K according to a rigid-rotor
    # harmonic oscillartor description for a frequency
    factor=planck_constant*frequency*speed_of_light/(boltzmann_constant*T)
    entropy=(factor*gas_constant/(math.exp(factor)-1.0)-gas_constant*math.log(1.0-math.exp(-factor)))/J_to_H
    return entropy
################ Grab information  from .out #####################
frequencies=[] #Vibrational frequencies
norm_term=False #Normal terminal variable
for line in f1:
    d=line.split()
    #Grab frequencies
    if line[0:15]==' Frequencies --':
        for i in range(2,5): #Loop through frequencies
            try:
                if float(d[i])<0: #Check for imiginary frequencies
                    sys.stdout.write('Imiginary freq. discovered: '+"{:.4f}".format(float(d[i]))+" cm-1\n")
                else:
                    frequencies.append(float(d[i]))
            except IndexError:
                pass
    #Check for normal termination
    elif line[0:19]==' Normal termination': #String if job is completed properly
        norm_term=True
    #Check for new job start
    elif line[0:29]==' This is part of the Gaussian': #Start of new job
        norm_term=False
    #Grab molecular mass
    elif line[0:16]==' Molecular mass:':
        molecular_mass=float(d[2])
    #Grab multiplicity
    elif 'Multiplicity' in d:
        #Double security
        if d[0]=='Charge' and d[1]=='=' and d[3]=='Multiplicity':
            multiplicity=int(d[5])
    #Grab SCF energy - last one sticks
    elif line[0:10] ==' SCF Done:':
        scf_energy=float(d[4])
    #Grab rotational temperature(s)
    elif line[0:24]==' Rotational temperatures':
        try:
            rotemp=[float(d[3]),float(d[4]),float(d[5])]
        except ValueError:
            rotemp=None
            if d.find('********'):
                linear_warning=True
                sys.exit('Molecule is linear. Not compatible with software yet.')
    elif line[0:23]==' Rotational temperature':
        rotemp=[float(d[3])]
    #Grab rotational symmetry number
    elif line[0:27]==' Rotational symmetry number':
        symmno=int(d[3].split(".")[0])
    #ZPE correction from .out
    elif line[0:23]==' Zero-point correction=':
        zpe_gauss=float(d[2])
    elif line[0:17]==' Full point group':
        if d[3]=='D*H' or d[3]=='C*V':
            linear=True

if not norm_term: #Check if the job has normal termination
    print('Job does not have normal termination')
f1.close()

######CALCULATIONS ARE DONE HERE###########
if len(frequencies)==0: #Checking whether one has obtained any pos. freqs.
    sys.exit('Error! No frequencies found.\n')
#Calculating properties
#u=Internal energy, s = Entropy
zpe=calc_zpe(frequencies) #Zero-Point Energy (vibrational)
u_rot=calc_rotational_energy(zpe,T,linear) #Internal rot. energy
u_vib=calc_vibrational_energy(frequencies,T) #Internal vib. energy
s_rot=calc_rotational_entropy(zpe,linear,T,rotemp,symmno) #rot. entropy
u_trans=calc_translational_energy(T) #Internal trans. energy
s_trans=calc_translational_entropy(molecular_mass,T) #trans. entropy
s_elec=calc_electronic_entropy(multiplicity) #elec. entropy

#Next up: Vibrational entropy
vibrations_s=[] #List containing entropy for each freq.
for freq in frequencies:
    #Truhlar method - frequencies under cutoff_freq are changed to cutoff_freq
    if freq<cutoff_freq:
        vibrations_s.append(calc_rrho_entropy(cutoff_freq,T))
    else:
        vibrations_s.append(calc_rrho_entropy(freq,T))
s_vib=sum(vibrations_s) #vibrational entropy

#####Summing up############
# H = scf_energy  + u_rot(T) + u_vib(T) + u_trans(T)+PV
# at 0K: H=scf_energy+zpe
# Hcorr= u_rot(T) + u_vib(T) + u_trans(T)
# S = s_rot(T) + s_vib(T,cutoff_freq) + s_trans(T)
# G = H - T*S
# Gcorr = Hcorr - T*S
# G without electronic energy = G - scf_energyi
pv=gas_constant*T/J_to_H #Pressure*Volume
H=scf_energy+u_rot+u_vib+u_trans+pv
S=s_rot+s_vib+s_trans+s_elec
G=H-T*S
Gcorr=G-scf_energy
#Printing thermic correction (Gcorr)
print('Thermic correction: '+str(Gcorr)+' H (a.u.)')
print('Zero-point Energy: (from Gaussian): '+str(zpe_gauss)+' H (a.u.)')
print('Thermodynamic properties printed in file: '+sys.argv[1][0:-4]+name_extension)
#Write output file
f_new=open(sys.argv[1][0:-4]+name_extension,'w')
f_new.write('************* Job completed ***************\n')
f_new.write('Parameters used: \n')
f_new.write('\t- Temperature was set to %4s K\n' % (T))
f_new.write('\t- Cutoff frequency was set to %4s cm-1\n' % (cutoff_freq))
f_new.write('\nCalculated properties: \n')
f_new.write('E(SCF) = '+str(scf_energy)+' H\n')
f_new.write('E(SCF) = '+str(scf_energy*H_to_kcal) +' kcal/mol\n')
f_new.write('ZPE (calculated) = '+str(zpe)+' H\n')
f_new.write('ZPE (from Gaussian) = '+str(zpe_gauss)+' H\n')
f_new.write('H = '+str(H)+' H\n')
f_new.write('H corr. (from temp.) = '+str(H-scf_energy)+' H\n')
f_new.write('T*S = '+str(T*S)+' H\n')
f_new.write('G = '+str(G)+' H\n')
f_new.write('G = '+str(G*H_to_kcal)+' kcal/mol\n')
f_new.write('Thermic correction = '+str(Gcorr)+' H\n')
f_new.write('Thermic correction = '+str(Gcorr*H_to_kcal)+' kcal/mol')
f_new.close()
