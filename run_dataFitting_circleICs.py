# ====================================================================================
# Script to fit the CA to the intermittent trial data from Bochovsky et al
# ====================================================================================
import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import pickle
from tqdm import tqdm
from itertools import product
import os
import sys
import re
import shutil
import multiprocessing as mp
from itertools import product
from lmfit import minimize, Parameters

sys.path.append('./utils/')
import myUtils as utils
from OnLatticeModel import OnLatticeModel
from fittingUtils import residual, PerturbParams, ComputeRSquared, LoadPatientData, PatientToOutcomeMap, \
    GetBestFit, GenerateFitSummaryDf, GenerateFitSummaryDf_AllPatients, PlotParameterDistribution_PatientCohort, \
    PlotFits

# ================================ Script setup ==========================================
dataDir = "../paper/data/Bruchovsky_et_al/"
patientIdList = [12,36,75,105,16,88,19,109,22,31,41,6,50,52]
# patientIdList = [int(re.findall(r'\d+',x)[0]) for x in os.listdir(dataDir)]
nFits = 10
perturbICs = True
runParallel = True
nProcesses = 22
outDir = './fits_circle/'

# Parameterise fitting algorithm
eps_data = 1.
optimiser_kws = {'method':'basinhopping', 'niter':75,
                 'nan_policy':'omit', 'disp':True}
solver_kws = {'nReplicates':25, 'initialSeedingType':'circle', "xDim":150, "yDim":150}
params = Parameters()
params.add('cost', value=0, min=0, max=1, vary=True)
params.add('turnover', value=0, min=0, max=1, vary=True)
params.add('initialSize', min=0.1, max=1., vary=True)
params.add('rFrac', min=1e-5, max=0.25, vary=True)
# ============================= Auxillary Functions ======================================
def FitModel(job):
    patientId, fitId, params, outDir = job['patientId'], job['fitId'], job['params'], job['outDir']
    dataDf = LoadPatientData(patientId, dataDir)
    summaryOutDir = os.path.join(outDir, "patient%d/"%(patientId))
    modelOutDir = os.path.join(summaryOutDir, "fitId%d/"%(fitId))
    job['outDir'] = modelOutDir
    if os.path.isfile(os.path.join(summaryOutDir,"fitObj_patient_%d_fit_%d.p"%(patientId, fitId))): return 0
    utils.mkdir(modelOutDir)
    seed = int.from_bytes(os.urandom(4), byteorder='little')
    np.random.seed(seed)
    tmpModel = OnLatticeModel()
    tmpModel.SetParams(**job, **solver_kws)  # modelConfigDic['outDir'] = currOutDir
    if perturbICs: params = PerturbParams(params)
    try:
        fitObj = minimize(residual, params, args=(0, dataDf, eps_data, tmpModel, "PSA", solver_kws), **optimiser_kws)
        # Plot best fit
        myModel = OnLatticeModel()
        myModel.SetParams(**fitObj.params.valuesdict(), **solver_kws)
        myModel.SetParams(outDir=modelOutDir)
        myModel.Simulate(treatmentScheduleList=utils.ExtractTreatmentFromDf(dataDf), max_step=1, **solver_kws)
        fig, ax = plt.subplots(1, 1, figsize=(10, 6))
        plt.plot(dataDf.Time, dataDf.PSA, linestyle='none', marker='x')
        myModel.Plot(ylim=2.,ax=ax)
        plt.savefig(os.path.join(summaryOutDir,"patient_%d_fit_%d.png" % (patientId, fitId)))
        plt.close()

        # Save fit
        fitObj.patientId = patientId
        fitObj.fitId = fitId
        fitObj.seed = seed
        fitObj.eps_data = eps_data
        fitObj.rSq = ComputeRSquared(fitObj,dataDf)
        pickle.dump(obj=fitObj, file=open(os.path.join(summaryOutDir,"fitObj_patient_%d_fit_%d.p"%(patientId, fitId)), "wb"))
        shutil.rmtree(modelOutDir)
    except:
        pass
# ================================= Main ================================================
pool = mp.Pool(processes=nProcesses,maxtasksperchild=1) if runParallel else None

jobList = []
for patientId,fitId in tqdm(product(patientIdList,range(nFits)),disable=runParallel):
    job = {'patientId':patientId, 'fitId':fitId, 'params':params, 'outDir':outDir}
    jobList.append(job)
    if not runParallel: FitModel(job)
if runParallel: list(tqdm(pool.imap(FitModel, jobList), total=len(jobList)))

# Analyse data
paramList = ["initialSize",'rFrac','cost',"turnover"]
fitDir = outDir
dataToAnalyse = GenerateFitSummaryDf_AllPatients(patientIdList,fitDir,dataDir=dataDir)
dataToAnalyse.to_csv(os.path.join(fitDir, "fitSummaryDf.csv"))

# Clean up the fitting directory
for patientId in tqdm(patientIdList):
    for fitId in range(nFits):
        summaryOutDir = os.path.join(outDir, "patient%d/" % (patientId))
        modelOutDir = os.path.join(summaryOutDir, "fitId%d/" % (fitId))
        try:
            shutil.rmtree(modelOutDir)
        except:
            pass