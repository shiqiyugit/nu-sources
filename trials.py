#!/usr/bin/env python

import csky as cy
import numpy as np
import pandas as pd
import glob
import healpy as hp
import pickle, datetime, socket
import histlite as hl
now = datetime.datetime.now
import matplotlib.pyplot as plt
import click, sys, os, time
import config as cg
import utils
flush = sys.stdout.flush
hp.disable_warnings()

repo, ana_dir, base_dir, job_basedir = cg.repo, cg.ana_dir, cg.base_dir, cg.job_basedir

class State (object):
    def __init__ (self, ana_name, ana_dir, save, base_dir, job_basedir, mask_deg=0, mask_radius=0, nsrc_tomask=0, nth_tomask=None, mask_self=False):
        self.ana_name, self.ana_dir, self.save, self.job_basedir, self.mask_deg = ana_name, ana_dir, save, job_basedir, mask_deg
        self.source_r = mask_radius
        self.nsrc_tomask = nsrc_tomask
        self.nth_tomask=nth_tomask
        self.base_dir = base_dir
        self._ana = None
        self.mask_self=mask_self
        self.mask_plane=False

        if self.mask_deg != 0:
            self.mask_plane=True
            self.ana_name += '_masking_GP{}'.format(round(self.mask_deg,1))
        if self.source_r !=0:
            self.ana_name += '_{}src{}'.format(int(nsrc_tomask), round(self.source_r,1))
        if self.nth_tomask!=None or self.mask_self:
            self.ana_name += '_selfmask'
    @property
    def ana (self):
        if self._ana is None:
            specs = cy.selections.ESTESDataSpecs.ESTES_2011_2021

            version = 'version-001-p00'
            repo.clear_cache()

            if self.mask_deg != 0 or self.source_r >0:
                df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
                idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
                idx2 = df_orig['neutrino_expectation_estes'] >=0.3 #1557

                df = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)
                decs=[]
                ras=[]
                DECdeg=df["DECdeg"]
                RAdeg=df["RAdeg"]
                masks=[]
                if self.nsrc_tomask>0:
                    masks=[i for i in range(self.nsrc_tomask)]
                if self.nth_tomask not in masks:
                    masks.append(self.nth_tomask)
                decs=DECdeg[masks]
                ras=RAdeg[masks]
                print("masking: ", decs)
                src_tomask=[cy.utils.Sources(dec=i, ra=j, deg=True) for i,j in zip(decs, ras)]
                ana = cy.get_analysis(repo, version, specs, 
#                                     space_bg_kw = {'bg_mc_weight':'bg_weight'}, energy_kw = {'bg_mc_weight': 'bg_weight'}, load_sig = True,
                                     dir = self.ana_dir, mask_plane=self.mask_plane, strip = self.mask_deg, mask_radius = np.radians(self.source_r), src_tomask=src_tomask)

            else:
                ana = cy.get_analysis(repo, version, specs, 
                                     space_bg_kw = {'bg_mc_weight':'bg_weight'}, energy_kw = {'bg_mc_weight': 'bg_weight'}, load_sig = True) 

            if self.save:
                cy.utils.ensure_dir (self.ana_dir)
                ana.save (self.ana_dir)
            ana.name = self.ana_name
            self._ana = ana
        return self._ana

    @property
    def state_args (self):
        return '--ana {} --ana-dir {} --base-dir {}'.format (
            self.ana_name, self.ana_dir, self.base_dir)

pass_state = click.make_pass_decorator (State)

@click.group (invoke_without_command=True, chain=True)
@click.option ('-a', '--ana', 'ana_name', default='ESTES', help='Dataset title')
@click.option ('--ana-dir', default=ana_dir, type=click.Path ())
@click.option ('--job_basedir', default=job_basedir, type=click.Path ())
@click.option ('--save/--nosave', default=False)
@click.option ('--base-dir', default=base_dir,
               type=click.Path (file_okay=False, writable=True))
@click.option ('--mask_deg', default=0, type=float)
@click.option ('--source_r', default=0, type=float)
@click.option ('--nsrc_tomask', default=0, type=int)
@click.option ('--nth_tomask', default=0, type=int)
@click.option ('--mask_self', default=False)
@click.pass_context
def cli (ctx, ana_name, ana_dir, save, base_dir, job_basedir, mask_deg, source_r, nsrc_tomask, nth_tomask, mask_self):
    ctx.obj = State.state = State (ana_name, ana_dir, save, base_dir, job_basedir, mask_deg, source_r, nsrc_tomask, nth_tomask, mask_self)


@cli.resultcallback ()
def report_timing (result, **kw):
    exe_t1 = now ()
    print ('c11: end at {} .'.format (exe_t1))
    print ('c11: {} elapsed.'.format (exe_t1 - exe_t0))

@cli.command ()
@pass_state
def setup_ana (state):
    state.ana

@cli.command()
@click.option('--n-trials', default=1000, type=int, help='Number of bg trials to run')
@click.option ('--corona', default=True, type=bool)
@click.option ('--gamma', default=0., type=float, help='Spectral Index to inject')
@click.option ('--sigsub/--nosigsub', default=True, type=bool, 
    help='Include Signal Subtraction in LLH')
@click.option ('--dec_deg',   default=0, type=float, help='Declination in deg')
@click.option ('--ra_deg',   default=0, type=float, help='Declination in deg')
@click.option ('--seed', default=None, type=int, help='Seed for scrambeling')
@click.option ('--cpus', default=1, type=int, help='Number of CPUs to use')
@click.option ('--nsigma', default=None, type=float, help='Do DP trials')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')      
@pass_state
def do_ps_sens ( 
        state, n_trials, corona, gamma, sigsub, ra_deg, dec_deg, seed, 
        cpus, nsigma, cutoff, logging=True):
    """
    Do seeded point source sensitivity and save output.  Useful for quick debugging not for 
    large scale trial calculations.
    """
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed) 
    ana = state.ana
    sindec = np.sin(np.radians(dec_deg))
    sinra = np.sin(np.radians(ra_deg))
    def get_PS_sens(sindec, sinra=sinra, n_trials=n_trials, corona=corona, gamma=gamma, mp_cpu=cpus):

        def get_tr(sindec, sinra, corona, gamma, cpus):
            src = cy.utils.sources(np.arcsin(sinra), np.arcsin(sindec), deg=False)
            cutoff_GeV = cutoff * 1e3
            if corona and gamma==0:
                conf = cg.get_seyfert_ps_conf(
                    src, 3.7, 42.39, sigsub=sigsub)
            elif corona and gamma >0:
                conf = cg.get_seyfert_ps_conf(
                    src,  3.7, 42.39, sigsub=sigsub)
                conf.pop('energy')    
            else:
                conf = cg.get_ps_conf(
                src=src, gamma=gamma, cutoff_GeV=cutoff_GeV, sigsub=sigsub)

            tr = cy.get_trial_runner(ana=ana, conf=conf, mp_cpus=cpus)
            return tr, src

        tr, src = get_tr(sindec, sinra, corona, gamma, cpus)
        print('Performing BG Trails at RA: {}, DEC: {}'.format(src.ra_deg, src.dec_deg))
        bg = cy.dists.Chi2TSD(tr.get_many_fits(n_trials, mp_cpus=cpus, seed=seed))
        if nsigma:
            beta = 0.5
            ts = bg.isf_nsigma(nsigma)
            n_sig_step = 25
        else:
            beta = 0.9
            ts = bg.median()
            n_sig_step = 6
        sens = tr.find_n_sig(
            # ts, threshold
            ts,
            # beta, fraction of trials which should exceed the threshold
            beta,
            # n_inj step size for initial scan
            n_sig_step=n_sig_step,
            # this many trials at a time
            batch_size=2500,
            # tolerance, as estimated relative error
            tol=.025,
            first_batch_size = 250,
            mp_cpus=cpus,
            seed=seed
        )
        if corona:
            sens['flux'] = tr.to_E2dNdE (sens['n_sig'],  customflux=True)
        else:
            sens['flux'] = tr.to_E2dNdE (sens['n_sig'] )
        print(sens['flux'])
        return sens

    t0 = now ()
    print ('Beginning calculation at {} ...'.format (t0))
    flush ()
    sens = get_PS_sens (sindec, sinra, corona=corona, gamma=gamma, n_trials=n_trials, mp_cpu=cpus) 
    
    sens_flux = np.array(sens['flux'])
    if corona:
          out_dir = cy.utils.ensure_dir('{}/Ecorona/{}/dec/{:+08.3f}/'.format(
                state.base_dir,'sigsub' if sigsub else 'nosigsub',  dec_deg))
    else:
        out_dir = cy.utils.ensure_dir('{}/E{}/{}/dec/{:+08.3f}/'.format(
            state.base_dir, int(gamma*100), 'sigsub' if sigsub else 'nosigsub',  dec_deg))
    if nsigma:
        out_file = out_dir + 'dp_{}sigma.npy'.format(nsigma)
    else:
        out_file = out_dir + 'sens.npy'
    print(sens_flux)
    np.save(out_file, sens_flux)
    t1 = now ()
    print ('Finished sens at {} ...'.format (t1))

@cli.command()
@click.option('--n-trials', default=1000, type=int, help='Number of trails to run')
@click.option ('-n', '--n-sig', default=0, type=float, help = 'Number of signal events to inject')
@click.option ('--poisson/--nopoisson', default=True, 
    help = 'toggle possion weighted signal injection')
@click.option ('--sigsub/--nosigsub', default=False, type=bool, 
    help='Include Signal Subtraction in LLH')
@click.option ('--dec_deg',   default=0, type=float, help='Declination in deg')
@click.option ('--gamma', default=2.0, type=float, help='Spectral Index to inject')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')      
@click.option ('--seed', default=None, type=int, help='Trial injection seed')
@click.option ('--cpus', default=1, type=int, help='Number of CPUs to use')
@pass_state
def do_ps_trials ( 
        state, dec_deg, n_trials, gamma, cutoff, n_sig, 
        poisson, sigsub, seed, cpus, logging=True):
    """
    Do seeded point source trials and save output in a structured dirctory based on paramaters
    Used for final Large Scale Trail Calculation
    """
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed) 
    print('Seed: {}'.format(seed))
    dec = np.radians(dec_deg)
    replace = False 
    sindec = np.sin(dec)
    t0 = now ()
    ana = state.ana
    src = cy.utils.Sources(dec=dec, ra=0)
    cutoff_GeV = cutoff * 1e3
    dir = cy.utils.ensure_dir ('{}/ps/'.format (state.base_dir, dec_deg))
    a = ana[0]

    def get_tr(sindec, gamma, cpus, sigsub=sigsub, ana_name=state.ana_name):
        src = cy.utils.sources(0, np.arcsin(sindec), deg=False)
        conf = cg.get_ps_conf(
            src=src, gamma=gamma, cutoff_GeV=cutoff_GeV, sigsub=sigsub, ana_name=ana_name)
        tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        return tr, src

    tr, src = get_tr(sindec, gamma=gamma, cpus=cpus, sigsub=sigsub)
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()
    if n_sig:
        out_dir = cy.utils.ensure_dir (
            '{}/ps/trials/{}/{}/{}/gamma/{:.3f}/cutoff_TeV/{:.0f}/dec/{:+08.3f}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name,
                'sigsub' if sigsub else 'nosigsub',
                'poisson' if poisson else 'nonpoisson',
                 gamma, cutoff, dec_deg, n_sig))
    else:
        out_dir = cy.utils.ensure_dir ('{}/ps/trials/{}/bg/dec/{:+08.3f}/'.format (
            state.base_dir, state.ana_name, dec_deg))
    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)

@cli.command()
@click.option('--n-trials', default=1000, type=int, help='Number of trails to run')
@click.option ('-n', '--n-sig', default=0, type=float, help = 'Number of signal events to inject')
@click.option ('--poisson/--nopoisson', default=True,
    help = 'toggle possion weighted signal injection')
@click.option ('--sigsub/--nosigsub', default=False, type=bool,
    help='Include Signal Subtraction in LLH, only when using scrambled data as bkg. Sigsub=False will automatically switch to MC bkg. No cross use!')
@click.option ('--n-src', 'n_src', default=0, type=int, help="the nth src to run")
@click.option ('--seed', default=None, type=int, help='Trial injection seed')
@click.option ('--cpus', default=1, type=int, help='Number of CPUs to use')
@click.option ('--gamma', default=0, type=float, help = 'gamma = 0 fit to corona flux; otherwise fit to powerlaw')
@pass_state
def do_seyfert_ps_trials (
        state, n_src, n_trials, n_sig,
        poisson, sigsub, seed, cpus, gamma, logging=True):
    """
    Do seeded point source trials and save output in a structured dirctory based on paramaters
    Used for final Large Scale Trail Calculation
    """
    ana = state.ana
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed)
    print('Seed: {}'.format(seed))

    df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
    idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
    idx2 = df_orig['neutrino_expectation_estes'] >= 0.3 #0.1557

    df = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)

    src_dist = df['DIST'][n_src]
    src_log_lumin = df['logL2-10-intr'][n_src]
    src_dec = df['DECdeg'][n_src]
    src_ra = df['RAdeg'][n_src]

    t0 = now ()

    def get_tr(dec, dist_mpc, log_lumin, cpus, sigsub=sigsub):
        src = cy.utils.sources(0, dec, deg=True)
        conf = cg.get_seyfert_ps_conf(
            src, dist_mpc, log_lumin, sigsub=sigsub, gamma=gamma)
        tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        return tr, src

    tr, src = get_tr(src_dec, src_dist, src_log_lumin, cpus=cpus, sigsub=sigsub)
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()
    if n_sig:
        out_dir = cy.utils.ensure_dir (
            '{}/ps/trials/{}/{}/{}/corona_{}/dec/{:+08.3f}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name,
                'sigsub' if sigsub else 'nosigsub',
                'poisson' if poisson else 'nonpoisson',
                'flux' if gamma==0 else 'powerlaw',
                src_dec, n_sig))
    else:
        out_dir = cy.utils.ensure_dir ('{}/ps/trials/{}/bg/corona_{}/dec/{:+08.3f}/'.format (
            state.base_dir, state.ana_name, 'flux' if gamma==0 else 'powerlaw', src_dec))
    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)

@cli.command ()
@click.option ('--fit/--nofit', default=False, help = 'Chi2 Fit or Not')
@click.option ('--dist/--nodist', default=True, help = 'Distribution is TSD or leave in arrays')
@click.option ('--inputdir', default=None, help = 'Option to set a read directory that isnt the base directory')
@click.option ('--outputname', default='corona', help = 'flux name append to sig file name')
@pass_state
def collect_ps_bg (state, fit,  dist, inputdir, outputname):
    """
    Collect all Background Trials and save in nested dict
    """
    kw = {}
    if outputname:
        df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
        idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
        idx2 = df_orig['neutrino_expectation_estes'] >= 0.3 #0.1557

        df = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)
        dec_degs = df['DECdeg']
    else:
        dec_degs = np.r_[-81:+81.01:2]
    if fit:
        TSD = cy.dists.Chi2TSD
        suffix = '_chi2'
    else:
        if dist:
            TSD = cy.dists.TSD
            suffix = 'TSD'
        else:
            suffix = ''
    if 'pi0' in outputname:
        outfile = '{}/gp_bg_ps/trials/{}/bg{}{}.dict'.format (
            state.base_dir, state.ana_name,  suffix, outputname)
    else:
        outfile = '{}/ps/trials/{}/bg{}{}.dict'.format (
            state.base_dir, state.ana_name,  suffix, outputname)
    bg = {}
    bgs = {}
    if inputdir:
        bg_dir = inputdir
    else:
        bg_dir = '{}/ps/trials/{}/bg'.format (
            state.base_dir, state.ana_name)
    for dec_deg in dec_degs:
        key = '{:+08.3f}'.format (dec_deg)
        flush ()
        print('{}/dec/{}/'.format(bg_dir, key))
        if dist == False:
            print('no dist') 
            post_convert = (lambda x: cy.utils.Arrays (x))
        else:
            post_convert = (lambda x: TSD (cy.utils.Arrays (x), **kw))
        bg_trials = cy.bk.get_all (
                '{}/dec/{}/'.format (bg_dir, key), '*.npy',
                merge=np.concatenate, post_convert=post_convert)
        if bg_trials is not False:
            bgs[float(key)] = bg_trials
    bg['dec'] = bgs
    print ('\rDone.' + 20 * ' ')
    flush ()
    print ('->', outfile)
    with open (outfile, 'wb') as f:
        pickle.dump (bg, f, -1)

@cli.command ()
@click.option ('--inputdir', default=None, help = 'Option to set a read directory that isnt the base directory')
@click.option ('--sigsub/--nosigsub', default=True, type=bool)
@click.option('--outputname', default=None, type=str, help='str append to output file')
@pass_state
def collect_ps_sig (state, inputdir, sigsub, outputname):
    """
    Collect all Signal Trials and save in nested dict
    """

    if sigsub:
        sigsub_str='sigsub'
    else:
        sigsub_str='nosigsub'

    if inputdir:
        sig_dir = inputdir
    else:
        sig_dir = '{}/ps/trials/{}/poisson/{}'.format (state.base_dir, state.ana_name, sigsub_str)

    sig = cy.bk.get_all (
        sig_dir, '*.npy', merge=np.concatenate, post_convert=cy.utils.Arrays)
    if 'pi0' in outputname:
        outfile = '{}/gp_bg_ps/trials/{}/sig_{}.dict'.format (state.base_dir, state.ana_name, outputname)
    else:
        outfile = '{}/ps/trials/{}/sig_{}.dict'.format (state.base_dir, state.ana_name, outputname)

    with open (outfile, 'wb') as f:
        pickle.dump (sig, f, -1)
    print ('->', outfile)


@cli.command ()
@click.option ('--gamma', default=None, type=float, help='Spectral Index to inject')
@click.option ('--nsigma', default=None, type=float, help='Number of sigma to find')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')      
@click.option ('--verbose/--noverbose', default=False, help = 'Noisy Output')
@click.option ('--fit', default=False, help = 'Fit the bkg dist to a chi2 or not?')
@click.option ('--inputdir', default=None, help = 'Option to set a read directory that isnt the base directory')
@click.option ('--corona', default=True, type=bool)
@click.option ('--sigsub/--nosigsub', default=False, type=bool)
@click.option ('--inputname', default='corona_flux', help = 'Noisy Output')
@pass_state
def find_ps_n_sig(state, nsigma, cutoff, gamma, verbose, fit, inputdir, corona, inputname,sigsub):
    """
    Calculate the Sensitvity or discovery potential once bg and sig files are collected
    """
    if not state.mask_self:
        ana = state.ana
       
    ana_name = state.ana_name
    if inputdir:
        base_dir = inputdir
    else: 
        base_dir = state.base_dir + '/ps/trials/' + ana_name

    if inputname.find('powerlaw') !=-1:
        gamma = 3
    else:
        gamma=0
    sigfile = '{}/sig_{}.dict'.format (base_dir, inputname)

    if inputname.find('nosigsub')  != -1 and inputname.find('scrambled') == -1:
        sigfile = '{}/sig_{}_nosigsub.dict'.format (base_dir, inputname)
    sig = np.load (sigfile, allow_pickle=True)
    fitstr = '_chi2'
    if fit:
        bgfile = '{}/bg_chi2{}.dict'.format (base_dir, inputname)
    else:
        bgfile = '{}/bgTSD{}.dict'.format (base_dir, inputname)
    bgfile = '{}/bgTSD{}.dict'.format (base_dir, inputname)
    print("loading sig and bg: ")
    print(sigfile)
    print(bgfile)
    sig = np.load (sigfile, allow_pickle=True)
    bg = np.load (bgfile, allow_pickle=True)
    if corona:
        df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
        idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] >-80)
        idx2 = df_orig['neutrino_expectation_estes'] >= 0.3 #0.1557

        cat = df_orig[idx &idx2].copy(deep=True)
        cat.sort_values(by='neutrino_expectation_estes', ascending=False,inplace=True)
        decs = cat['DECdeg']
        ras = cat['RAdeg']
        log_lumins = cat['logL2-10-intr']
        dist_mpcs = cat['DIST']
    else:
        decs = list(bg['dec'].keys())
    def get_n_sig(
                dec, gamma, ana, ra=0, dist_mpc = 0, log_lumin = 0,
                beta=0.9, nsigma=None, cutoff=cutoff, fit=fit, verbose=verbose, corona=corona):
        if cutoff == None:
            cutoff_GeV = np.inf
            cutoff = np.inf
        else:
            cutoff_GeV = cutoff*1e3

        if verbose:
            print("gamma, dec, cutoff: ", gamma, dec, cutoff)
        if corona:
            sig_trials = cy.bk.get_best(sig,  'dec', dec, 'nsig')  
            #print(sig_trials)
        else:
            sig_trials = cy.bk.get_best(sig,  'gamma', gamma, 'cutoff_TeV', 
                cutoff, 'dec', dec, 'nsig')    
        b = cy.bk.get_best(bg,  'dec', dec)
        if verbose:
            print("best bg, dec: ", b)
        src = cy.utils.sources(ra, dec, deg=True)
        if corona:
            conf = cg.get_seyfert_ps_conf(
                src, dist_mpc, log_lumin, sigsub=sigsub, gamma=gamma)

            if inputname.find('pi0') != -1:
                print("find sens/dp for gp template bgs")
                sigmas = ana[0].sig.sigma
                conf = cg.get_gp_bg_ps_conf(src=src, src_dist=dist_mpc, src_log_lumin=log_lumin,
                    template_str='pi0', sigmas = sigmas, cutoff_GeV=cutoff_GeV, base_dir=state.base_dir)

#            if gamma>0:
#                conf['flux']=cy.hyp.PowerLawFlux(gamma, energy_cutoff=cutoff_GeV)
#                conf.pop('energy')
#                conf.pop('corona_flux')
        else:
            conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)

        tr = cy.get_trial_runner(ana=ana, conf=conf)
            # determine ts threshold
        if nsigma !=None:
            #print('sigma = {}'.format(nsigma))
            if fit:
                ts = cy.dists.Chi2TSD(b).isf_nsigma(nsigma)
            else:
                ts = cy.dists.TSD(b).isf_nsigma(nsigma)
        else:
            #print('Getting sensitivity')
            ts = cy.dists.Chi2TSD(b).median()
        if verbose:
            print("Printing ts:")
            print(ts)

        # include background trials in calculation
        trials = {0: b}
        trials.update(sig_trials)
        result = tr.find_n_sig(ts, beta, max_batch_size=0, logging=verbose, trials=sig_trials)#syu
        if corona:
            if gamma>0:
                flux = tr.to_E2dNdE(result['n_sig'])
            else: 
                flux = tr.to_E2dNdE(result['n_sig'])#, acc_total = exp_nu, E0=1, unit=1, customflux=True)
        else:
            flux = tr.to_E2dNdE(result['n_sig'])

        if verbose:
            print("ts, beta, result['n_sig'], flux")
            print(ts, beta, result['n_sig'], flux)

        return flux , result['n_sig'], ts
    fluxs = []
    ns = []
    tss = []
    if fit:
        print('Fitting to a chi2')
        fit_str = 'chi2fit'
    else:
        print('Not fitting to a chi2 - using bkg trials')
        fit_str = 'nofit'    
    if nsigma:
        beta = 0.5
    else:
        beta = 0.9
    for i, dec in enumerate(decs):
        if state.mask_self:
            state.nth_tomask = i
            state._ana=None  # reset
            ana = state.ana
        if corona:
            f, n, ts = get_n_sig( ana=ana,
                ra = ras[i], dec=dec, gamma=gamma, dist_mpc = dist_mpcs[i], log_lumin =log_lumins[i] ,
                beta=beta, nsigma=nsigma, cutoff=cutoff,
                fit=fit, verbose=verbose)
        else:
            f, n, ts = get_n_sig(ana=ana,
                dec=dec, gamma=gamma, beta=beta, nsigma=nsigma, cutoff=cutoff,
                fit=fit, verbose=verbose)
        print("dec, n, flux, ts")
        print('{:.3} : {:.3} : {:.5}  : TS : {:.5}                                    '.format(
            dec, n, f, ts) , end='\r', flush=True)

        fluxs.append(f)
        ns.append(n)
        tss.append(ts)
    print("saving into:  ", base_dir)

    if nsigma:
          np.save(base_dir + '/ps_dp_{}sigma_flux_{}_{}.npy'.format(nsigma, inputname, fit_str), fluxs)
          np.save(base_dir + '/ps_dp_{}sigma_tss_{}_{}.npy'.format(nsigma, inputname, fit_str), tss)
          np.save(base_dir + '/ps_dp_{}sigma_nss_{}_{}.npy'.format(nsigma, inputname, fit_str), ns)
          np.save(base_dir + '/ps_dp_{}sigma_decs_{}_{}.npy'.format(nsigma, inputname, fit_str), decs)
    else:
        np.save(base_dir + '/ps_sens_flux_{}_{}.npy'.format(inputname, fit_str), fluxs)
        np.save(base_dir + '/ps_sens_tss_{}_{}.npy'.format(inputname, fit_str), tss)
        np.save(base_dir + '/ps_sens_nss_{}_{}.npy'.format(inputname, fit_str), ns)
        np.save(base_dir + '/ps_sens_decs_{}_{}.npy'.format(inputname, fit_str), decs)

    '''
    if corona:
      if gamma >0:
          gammastr='powerlaw'
      else:
          gammastr='flux'

      if nsigma:
          np.save(base_dir + '/ps_dp_{}sigma_flux_corona_{}_{}.npy'.format(
              nsigma, gammastr,  fit_str), fluxs)
          np.save(base_dir + '/ps_dp_{}sigma_tss_corona_{}_{}.npy'.format(nsigma,  gammastr, fit_str), tss)
          np.save(base_dir + '/ps_dp_{}sigma_nss_corona_{}_{}.npy'.format(nsigma, gammastr, fit_str), ns)
          np.save(base_dir + '/ps_dp_{}sigma_decs_corona_{}_{}.npy'.format(nsigma, gammastr,fit_str), decs)
      else:
        np.save(base_dir + '/ps_sens_flux_corona_{}_{}.npy'.format(gammastr, fit_str), fluxs)
        np.save(base_dir + '/ps_sens_nss_corona_{}_{}.npy'.format(gammastr,fit_str), ns)
        np.save(base_dir + '/ps_sens_decs_corona_{}_{}.npy'.format(gammastr, fit_str), decs)

    else:
      if nsigma:
        np.save(base_dir + '/ps_dp_{}sigma_flux_E{}_{}.npy'.format(
            nsigma, int(gamma * 100), fit_str), fluxs)
        np.save(base_dir + '/ps_dp_{}sigma_tss_E{}_{}.npy'.format(nsigma, int(gamma * 100), fit_str), tss)
        np.save(base_dir + '/ps_dp_{}sigma_nss_E{}_{}.npy'.format(nsigma, int(gamma * 100), fit_str), ns)
        np.save(base_dir + '/ps_dp_{}sigma_decs_E{}_{}.npy'.format(nsigma, int(gamma * 100), fit_str), decs)
      else:
        np.save(base_dir + '/ps_sens_flux_E{}_{}.npy'.format(int(gamma * 100), fit_str), fluxs)
        np.save(base_dir + '/ps_sens_nss_E{}_{}.npy'.format(int(gamma * 100), fit_str), ns)
        np.save(base_dir + '/ps_sens_decs_E{}_{}.npy'.format(int(gamma * 100), fit_str), decs)
      '''

@cli.command()
@click.option('--n-trials', default=1000, type=int)
@click.option ('-n', '--n-sig', default=0, type=float)
@click.option ('--poisson/--nopoisson', default=True)
@click.option ('--sigsub/--nosigsub', default=False, type=bool,
    help='Include Signal Subtraction in LLH')
@click.option ('--catalog',   default='seyfert_southernsky' , type=str, help='Stacking Catalog, SNR, PWN or UNID')
@click.option ('--gamma', default=0, type=float, help = 'gamma = 0 fit to corona flux; otherwise fit to powerlaw')
@click.option ('--weightedfit', default=False, type=bool)
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')
@click.option ('--seed', default=None, type=int)
@click.option ('--cpus', default=1, type=int)
@click.option ('--corona', default=True, type=bool, help="inject with corona flux or not")
@click.option ('--debug', default=False, type=bool)
@click.option ('--nu_max', default = 6, type=float)
@click.option ('--nu_min', default =0.3, type=float)
@click.option ('--gpbg', default=False, type=bool)
@pass_state
def do_seyfert_stacking_trials (
        state, n_trials, gamma, cutoff, catalog,
        n_sig,  poisson, sigsub, seed, cpus, corona, debug, weightedfit, nu_min, nu_max, gpbg, logging=True):
    """
    Do trials from a stacking catalog
    """
    catalog = catalog.lower()
    print('Catalog: {}'.format(catalog))
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed) 
    ana = state.ana
    df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
    idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
    if debug:
        idx2 = df_orig['neutrino_expectation_estes'] >=3#0.1557
    else:
        idx2 =np.logical_and(df_orig['neutrino_expectation_estes'] >=nu_min, df_orig['neutrino_expectation_estes']<=nu_max)
    cat = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)
    print(sum(cat['neutrino_expectation_estes']))
    print(len(cat), cat[['CTPT_NAME','DECdeg', 'DIST', 'F2-10-intr', 'F14-195-intr','logNH', 'neutrino_expectation_estes']])
    src_dist = cat['DIST']
    src_log_lumin = cat['logL2-10-intr']
    cutoff_GeV = cutoff * 1e3
    weights = None
    weighted_src = None
    if weightedfit:
        weights = cat['F2-10-intr']
        print("weights: ", weights)
        weighted_src = cy.utils.Sources(dec=cat['DECdeg'], ra=cat['RAdeg'], deg=True, weight = weights)

    src = cy.utils.Sources(dec=cat['DECdeg'], ra=cat['RAdeg'], deg=True) 
    sigmas = ana[0].sig.sigma
    def get_tr(src, gamma, src_dist, src_log_lumin, corona=corona, cpus=cpus, weighted_src = None, sigsub=sigsub, gpbg=gpbg, temp='pi0'):
        if gpbg:
            if corona:
                gp_conf = cg.get_gp_bg_ps_conf(src=src, src_dist=dist_mpc, src_log_lumin=log_lumin, weighted_src = weighted_src,
                          template_str=temp, sigmas = sigmas, cutoff_GeV=cutoff_GeV, base_dir=state.base_dir, mcbg=mcbg, gamma=gamma)
            else:
                gp_conf = cg.get_gp_bg_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)

            tr = cy.get_trial_runner(gp_conf, inj_conf = gp_conf, update_bg = True, sigsub=sigsub, ana=ana, mp_cpus=cpus, seed=seed)
        else:
            if corona:
                conf = cg.get_seyfert_ps_conf(src, src_dist, src_log_lumin, gamma, weighted_src = weighted_src, sigsub=sigsub)
            else:
                conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)
            tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        return tr
    
    tr = get_tr(src, gamma,src_dist,src_log_lumin, corona, cpus, weighted_src)
    t0 = now ()
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging, sigsub=sigsub)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()
    catalog+=str(len(cat))
    if n_sig>0:
        if corona:
            out_dir = cy.utils.ensure_dir (
                '{}/stacking/trials/{}/catalog/{}/{}/{}/corona{}{}/cutoff_TeV/{:.0f}/nsig/{:08.3f}'.format (
                 state.base_dir, state.ana_name, catalog,
                 'sigsub' if sigsub else 'nosigsub',
                 'poisson' if poisson else 'nonpoisson',
                 '_powerlaw' if gamma>0 else '_flux', 
                 '_weightedfit' if weightedfit else '',
                 cutoff,  n_sig))
        else:
            out_dir = cy.utils.ensure_dir (
                '{}/stacking/trials/{}/catalog/{}/{}/{}/gamma/{:.3f}/cutoff_TeV/{:.0f}/nsig/{:08.3f}'.format (
                    state.base_dir, state.ana_name, catalog,
                    'sigsub' if sigsub else 'nosigsub',
                    'poisson' if poisson else 'nonpoisson',
                     gamma, cutoff,  n_sig))
    else:
        out_dir = cy.utils.ensure_dir ('{}/stacking/trials/{}/catalog/{}/bg/{}{}{}/'.format (
            state.base_dir, state.ana_name, catalog, 
            'corona' if corona else '', 
            '_powerlaw' if gamma>0 else '_flux',
            '_weightedfit' if weightedfit else ''))

    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)


@cli.command()
@click.option('--n-trials', default=1000, type=int)
@click.option ('-n', '--n-sig', default=0, type=float)
@click.option ('--poisson/--nopoisson', default=True)
@click.option ('--catalog',   default='snr' , type=str, help='Stacking Catalog, SNR, PWN or UNID')
@click.option ('--gamma', default=2.0, type=float, help = 'Spectrum to Inject')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')
@click.option ('--seed', default=None, type=int)
@click.option ('--cpus', default=1, type=int)
@pass_state
def do_stacking_trials (
        state, n_trials, gamma, cutoff, catalog,
        n_sig,  poisson, seed, cpus, logging=True):
    """
    Do trials from a stacking catalog
    """
    catalog = catalog.lower()
    print('Catalog: {}'.format(catalog))
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed) 
    print(seed)
    ana = state.ana
    catalog_file = os.path.join(
        cg.catalog_dir, '{}_ESTES_12.pickle'.format(catalog))
    cat = np.load(catalog_file, allow_pickle=True)
    src = cy.utils.Sources(dec=cat['dec_deg'], ra=cat['ra_deg'], deg=True)
    cutoff_GeV = cutoff * 1e3
    def get_tr(src, gamma, cpus):
        conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)
        tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        return tr
    tr = get_tr(src, gamma, cpus)
    t0 = now ()
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()
    if n_sig:
        out_dir = cy.utils.ensure_dir (
            '{}/stacking/trials/{}/catalog/{}/{}/gamma/{:.3f}/cutoff_TeV/{:.0f}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name, catalog,
                'poisson' if poisson else 'nonpoisson',
                 gamma, cutoff,  n_sig))
    else:
        out_dir = cy.utils.ensure_dir ('{}/stacking/trials/{}/catalog/{}/bg/'.format (
            state.base_dir, state.ana_name, catalog))
    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)

@cli.command()
@click.option('--n-trials', default=5000, type=int)
@click.option ('--catalog',   default='seyfert_southernsky' , type=str, help='Stacking Catalog, SNR, PWN or UNID')
@click.option ('--gamma', default=0.0, type=float, help = 'gamma = 0 fit to corona; otherwise: Spectrum to Inject')
@click.option ('--corona', default=True, type=bool, help = 'Inject corona model or not')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')
@click.option ('--seed', default=None, type=int)
@click.option ('--cpus', default=2, type=int)
@click.option ('--nsigma', default=3, type=float)
@click.option ('--nu_max', default = 1000, type=float)
@pass_state
def do_stacking_sens (
        state, n_trials, gamma, cutoff, catalog, nu_max,
        seed, cpus, nsigma,logging=True, corona=True):
    """
    Do senstivity calculation for stacking catalog.  Useful for quick numbers, not for
    analysis level numbers of trials
    """

    catalog = catalog.lower()
    print('Catalog: {}'.format(catalog))
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed) 
    print(seed)
    ana = state.ana
    df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
    idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
    idx2 = np.logical_and(df_orig['neutrino_expectation_estes'] >=0.3, df_orig['neutrino_expectation_estes'] < nu_max)

    cat = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)

    src_dist = cat['DIST']
    src_log_lumin = cat['logL2-10-intr']
    out_dir = cy.utils.ensure_dir ('{}/stacking/sens/{}/'.format (state.base_dir, catalog))
    cutoff_GeV = cutoff * 1e3

    src = cy.utils.Sources(dec=cat['DECdeg'], ra=cat['RAdeg'], deg=True)

    def get_tr(src, gamma, src_dist, src_log_lumin, corona, cpus):
        if corona:
            conf = cg.get_seyfert_ps_conf(src, src_dist, src_log_lumin, gamma)
        else:
            conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)
        tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        return tr
    tr = get_tr(src, gamma,src_dist,src_log_lumin, corona, cpus)

    t0 = now ()
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    bg = cy.dists.Chi2TSD(tr.get_many_fits (
      n_trials, n_sig=0, poisson=False, seed=seed, logging=logging))
    t1 = now ()
    print ('Finished bg trials at {} ...'.format (t1))
    if nsigma != 0:
        sens = tr.find_n_sig(
                        bg.isf_nsigma(nsigma), 
                        0.5, #percent above threshold (0.5 for dp)
                        n_sig_step=1,
                        batch_size = n_trials / 3, 
                        tol = 0.02,
                        seed =seed)
    else:
        sens = tr.find_n_sig(
                        bg.median(), 
                        0.9, #percent above threshold (0.9 for sens)
                        n_sig_step=1,
                        batch_size = n_trials / 3, 
                        tol = 0.02,
                        seed = seed)
    #sens['flux'] = tr.to_E2dNdE(sens['n_sig'], E0=100, unit=1e3)
    print ('Finished sens at {} ...'.format (t1))
    print (t1 - t0, 'elapsed.')
    print(sens['n_sig'])
    flush ()
    if nsigma != 0:
        out_file = out_dir + '{}gamma{}_dp{}_trials_{:07d}__seed_{:010d}.npy'.format('corona_' if corona else '', gamma, nsigma,n_trials, seed)
    else: 
        out_file = out_dir + '{}gamma{}_sens_trials_{:07d}__seed_{:010d}.npy'.format('corona_' if corona else '', gamma,n_trials, seed)
    np.save(out_file, sens)

@cli.command ()
@click.option ('--dist/--nodist', default=False)
@click.option('--inputdir', default=None, type=str, help='Option to Define an input directory outside of default')
@click.option('--outputname', default=None, type=str, help='str append to output file')
@pass_state
def collect_stacking_bg (state, dist, inputdir, outputname):
    """
    Collect all background trials for stacking into one dictionary for calculation of sensitvity
    """
    bg = {'cat': {}}
    cats = ['seyfert_southernsky'] #'snr' , 'pwn', 'unid']
    for cat in cats:
        if inputdir:
            bg_dir = inputdir
        else:
            bg_dir = cy.utils.ensure_dir ('{}/stacking/trials/{}/catalog/{}/bg/'.format (
                state.base_dir, state.ana_name, cat))
        print(bg_dir)
        print ('\r{} ...'.format (cat) + 10 * ' ', end='')
        flush ()
        if dist:
            bg = cy.bk.get_all (
                bg_dir, 'trials*npy',
                merge=np.concatenate, post_convert=(lambda x: cy.dists.Chi2TSD (cy.utils.Arrays (x))))
        else:
            bg = cy.bk.get_all (
                bg_dir, 'trials*npy',
                merge=np.concatenate, post_convert=cy.utils.Arrays )

        print ('\rDone.              ')
        flush ()
        if dist:
            outfile = '{}/stacking/{}_{}_bg_chi2.dict'.format (
                state.base_dir,  cat, outputname)
        else:
            outfile = '{}/stacking/{}_{}_bg.dict'.format (
                state.base_dir, cat, outputname)
        print ('->', outfile)
        with open (outfile, 'wb') as f:
            pickle.dump (bg, f, -1)

@cli.command ()
@click.option('--inputdir', default=None, type=str, help='Option to Define an input directory outside of default')
@click.option('--outputname', default=None, type=str, help='str append to output file')
@pass_state
def collect_stacking_sig (state, inputdir, outputname):
    """
    Collect all signal trials for stacking into one dictionary for calculation of sensitvity
    """
    cats = ['seyfert_southernsky'] #snr pwn unid'.split ()
    for cat in cats:
        if inputdir:
            sig_dir = inputdir
        else:
            sig_dir = '{}/stacking/trials/{}/catalog/{}/poisson'.format (
                state.base_dir, state.ana_name, cat)
        sig = cy.bk.get_all (
            sig_dir, '*.npy', merge=np.concatenate, post_convert=cy.utils.Arrays)
        outfile = '{}/stacking/{}_{}_sig.dict'.format (
            state.base_dir,  cat,outputname)
        with open (outfile, 'wb') as f:
            pickle.dump (sig, f, -1)
        print ('->', outfile)

@cli.command ()
@click.option ('--nsigma', default=None, type=float, help='Number of sigma to find')
@click.option ('--fit', default=False, help='Use chi2fit')
@click.option ('--nsig/--nonsig', default=True, help='Use nsig')
@click.option('--inputdir', default=None, type=str, help='Option to Define an input directory outside of default')
@click.option ('--verbose/--noverbose', default=False, help = 'Noisy Output')
@click.option ('--inputname', default='corona_flux', help = 'Noisy Output')
@click.option ('--sigsub/--nosigsub', default=False, type=bool)
@pass_state
def find_stacking_n_sig(state, nsigma, fit, inputdir, verbose, nsig, inputname, sigsub):
    """
    Calculate the Sensitvity or discovery potential once bg and sig files are collected
    Does all stacking catalogs
    """
    cutoff = None
    this_dir = os.path.dirname(os.path.abspath(__file__))
    ana = state.ana

    def find_n_sig_cat(src, weighted_src=None, gamma=3.0, beta=0.9, nsigma=None, cutoff=None, verbose=False, nsig=True, src_dist=None, src_log_lumin=None, fit=fit):
        # get signal trials, background distribution, and trial runner
        if cutoff == None:
            cutoff = np.inf
            cutoff_GeV = np.inf
        else:
            cutoff_GeV = 1e3 * cutoff
        if verbose:
            print(gamma, cutoff)
        if (gamma>0) and src_log_lumin is None:
            sig_trials = cy.bk.get_best(sig,  'gamma', gamma, 'cutoff_TeV', 
                cutoff, 'nsig')
        else:
            sig_trials = cy.bk.get_best(sig,  'cutoff_TeV',
                cutoff, 'nsig')
        b = bg
        if verbose:
            print(b)
        if 'corona' in inputname:
            conf = cg.get_seyfert_ps_conf(src, src_dist, src_log_lumin, gamma, weighted_src=weighted_src, sigsub=sigsub)
        else:
            abort()
        #if gamma > 0:
            #conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)
        #else:
        #    conf = cg.get_ps_conf(src=src, gamma=gamma, cutoff_GeV=cutoff_GeV)
        tr = cy.get_trial_runner(ana=ana, conf=conf)
            # determine ts threshold
        if nsigma !=None:
            #print('sigma = {}'.format(nsigma))
            if fit:
                ts = cy.dists.Chi2TSD(b).isf_nsigma(nsigma)
            else:
                ts = cy.dists.TSD(b).isf_nsigma(nsigma)
        else:
            #print('Getting sensitivity')
            ts = cy.dists.Chi2TSD(b).median()
        if verbose:
            print(ts)

        # include background trials in calculation
        trials = {0: b}
        trials.update(sig_trials)

        result = tr.find_n_sig(ts, beta, max_batch_size=0, logging=verbose, trials=trials)
        if gamma > 0:
            trueflux = cy.hyp.PowerLawFlux(gamma, energy_cutoff=cutoff_GeV)
            flux = tr.to_E2dNdE(result['n_sig'], flux=trueflux)
# E0=100, unit=1e3, flux=trueflux)
        else:
            flux = tr.to_E2dNdE(result['n_sig'])#, E0=100, unit=1e3)

        # return flux
        if verbose:
            print(ts, beta, result['n_sig'], flux)
        if nsig:
            return result['n_sig']
        else:
            return flux 

    fluxs = []
    if nsigma:
        beta = 0.5
    else:
        beta = 0.9
    cats = ['seyfert_southernsky'] #snr', 'pwn', 'unid']
    for cat in cats:
        if inputdir:
            indir = inputdir
        else:
            indir = state.base_dir + '/stacking/'
        base_dir = state.base_dir + '/stacking/'
        sigfile = '{}/{}_{}_sig.dict'.format (indir, cat,inputname)
        if not sigsub and inputname.find("scrambled") == -1:
            sigfile = '{}/{}_{}_nosigsub_sig.dict'.format (indir, cat,inputname)
        sig = np.load (sigfile, allow_pickle=True)
        bgfile = '{}/{}_{}_bg.dict'.format (indir, cat,inputname)
        bg = np.load (bgfile, allow_pickle=True)
        print('CATALOG: {}'.format(cat))

        df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
        idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
        idx2 = df_orig['neutrino_expectation_estes'] >= 0.3#0.1557

        df = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)

        src_dist = df['DIST']
        src_log_lumin = df['logL2-10-intr']
        weights = df['F2-10-intr']
        weighted_src = None
        if inputname.find('weighted') !=-1:
            weighted_src = cy.utils.Sources(dec=df['DECdeg'], ra=df['RAdeg'], deg=True, weight = weights)
        src = cy.utils.Sources(dec=df['DECdeg'], ra=df['RAdeg'], deg=True)

        name='flux'
        fitstr='_nofit'
        if fit:
            fitstr = '_chi2'
        if nsig:
            name='nss'
        if inputname.find('corona') == -1:
          print("inject with and fitted for gamma")
          abort()
          for gamma in sig['gamma'].keys():
            print ('Gamma: {}'.format(gamma))
            f = find_n_sig_cat(src, weighted_src = weighted_src, gamma=gamma, beta=beta, nsigma=nsigma, cutoff=cutoff, verbose=verbose, nsig=nsig)
            if nsig:
                print('Sensitvity nsig: ', f)
            else:
                print('Sensitvity Flux: {:.8}'.format(f))     
            fluxs.append(f)
       
            if nsigma:
                np.save(base_dir + '/stacking_{}_dp_{}sigma_{}_E{}_{}.npy'.format(cat, nsigma, name,  int(gamma * 100), inputname), fluxs)
            else:
                np.save(base_dir + '/stacking_{}_sens_{}_E{}_{}.npy'.format(cat, name, int(gamma * 100), inputname), fluxs)

        else: #corona model injection
            if inputname.find('powerlaw') !=-1:
                gamma = 3.0
            else:
                gamma=0

            f = find_n_sig_cat(src, weighted_src = weighted_src, gamma=gamma, beta=beta, nsigma=nsigma, cutoff=cutoff, verbose=verbose, nsig=nsig, src_dist=src_dist, src_log_lumin=src_log_lumin)
            if nsig:
                print('Sensitvity nsig: ', f)
            else:
                print(f)
                #print('Sensitvity Flux: {:.8}'.format(f))     
            fluxs.append(f)
       
            if nsigma:
                np.save(base_dir + '/stacking_{}_dp_{}sigma_{}_E{}_{}{}.npy'.format(cat, nsigma, name,  int(gamma * 100), inputname, fitstr), fluxs)
            else:
                np.save(base_dir + '/stacking_{}_sens_{}_E{}_{}{}.npy'.format(cat, name, int(gamma * 100),  inputname, fitstr), fluxs)


@cli.command()
@click.argument('temp', default="pi0")
@click.option('--n-trials', default=1000, type=int)
@click.option ('-n', '--n-sig', default=0, type=float)
@click.option ('--poisson/--nopoisson', default=True)
@click.option ('--seed', default=None, type=int)
@click.option ('--cpus', default=1, type=int)
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')
@pass_state
def do_gp_trials (
            state, temp, n_trials, n_sig,
            poisson, seed, cpus,
            cutoff, logging=True):
    """
    Do trials for galactic plane templates including Fermi bubbles
    and save output in a structured directory based on parameters
    """
    temp = temp.lower()
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed)
    print('Seed: {}'.format(seed))
    ana = state.ana
    cutoff_GeV = cutoff * 1e3
    sigmas = ana[0].sig.sigma

    def get_tr(temp):
        gp_conf = cg.get_gp_conf(
            template_str=temp, sigmas = sigmas, cutoff_GeV=cutoff_GeV, base_dir=state.base_dir)
        tr = cy.get_trial_runner(gp_conf, inj_conf = gp_conf['inj_conf'], update_bg = False, sigsub=False, ana=ana, mp_cpus=cpus, seed=seed)
        return tr

    tr = get_tr(temp)
    t0 = now ()
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()
    if temp =='fermibubbles':
        out_dir = cy.utils.ensure_dir (
            '{}/gp/trials/{}/{}/{}/cutoff/{}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name,
                temp,
                'poisson' if poisson else 'nonpoisson', cutoff,
                n_sig))
    else:
        out_dir = cy.utils.ensure_dir (
            '{}/gp/trials/{}/{}/{}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name,
                temp,
                'poisson' if poisson else 'nonpoisson',
                n_sig))

    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)


@cli.command()
@click.argument('temp', default="pi0")
@click.option('--n-trials', default=1000, type=int)
@click.option ('-n', '--n-sig', default=5, type=float)
@click.option ('--poisson/--nopoisson', default=True)
@click.option ('--seed', default=None, type=int)
@click.option ('--cpus', default=1, type=int)
@click.option ('--nsrc', default=1, type=int, help='nth src to run')
@click.option ('-c', '--cutoff', default=np.inf, type=float, help='exponential cutoff energy (TeV)')
@click.option ('--mcbg/--bgdata', default=False, help='bg from mc or scrambled data')
@click.option ('--gamma', default=0, type=float)
@pass_state
def do_gp_bg_ps_trials (
            state, temp, n_trials, n_sig,
            poisson, seed, cpus,
            cutoff, nsrc, mcbg, gamma, logging=True):

    temp = temp.lower()
    if seed is None:
        seed = int (time.time () % 2**32)
    random = cy.utils.get_random (seed)
    print('Seed: {}'.format(seed))
    ana = state.ana
    cutoff_GeV = cutoff * 1e3
    sigmas = ana[0].sig.sigma

    """
    Do seeded point source trials and save output in a structured dirctory based on paramaters
    Used for final Large Scale Trail Calculation
    """

    df_orig = pd.read_hdf('/cvmfs/icecube.opensciencegrid.org/users/shiqiyu/selected_xray_fullsky_seyferts_10yr.h5')
    idx = np.logical_and(df_orig['DECdeg'] < -5, df_orig['DECdeg'] > -80)
    idx2 = df_orig['neutrino_expectation_estes'] >=0.3

    df = df_orig[idx&idx2].sort_values(by='neutrino_expectation_estes', ascending=False).copy(deep=True)
    
    src_dist = df['DIST'][nsrc]
    src_log_lumin = df['logL2-10-intr'][nsrc]
    src_dec = df['DECdeg'][nsrc]
    src_ra = df['RAdeg'][nsrc]
#    src = cy.utils.Sources(dec=src_dec, ra = src_ra, deg=True)
#    print(src)   
    replace = False
    t0 = now ()
    a = ana[0]

    def get_gp_tr(temp, dec, dist_mpc, log_lumin, cpus=cpus, sigsub=False, mcbg=mcbg, gamma=gamma):
        src = cy.utils.sources(ra=0, dec=dec, deg=True)
        #conf = cg.get_seyfert_ps_conf(
        #    src, dist_mpc, log_lumin, sigsub=sigsub, gamma=gamma)
        #tr = cy.get_trial_runner(ana=ana, conf= conf, mp_cpus=cpus)
        gp_conf = cg.get_gp_bg_ps_conf(src=src, src_dist=dist_mpc, src_log_lumin=log_lumin,
            template_str=temp, sigmas = sigmas, cutoff_GeV=cutoff_GeV, base_dir=state.base_dir, mcbg=mcbg, gamma=gamma)
        tr_gp = cy.get_trial_runner(gp_conf, inj_conf = gp_conf, update_bg = True, sigsub=False, ana=ana, mp_cpus=cpus, seed=seed)
        return tr_gp

    tr = get_gp_tr(temp,src_dec, dist_mpc=src_dist, log_lumin=src_log_lumin)
    t0 = now ()
    print ('Beginning trials at {} ...'.format (t0))
    flush ()
    trials = tr.get_many_fits (
        n_trials, n_sig=n_sig, poisson=poisson, seed=seed, logging=logging)
    t1 = now ()
    print ('Finished trials at {} ...'.format (t1))
    print (trials if n_sig else cy.dists.Chi2TSD (trials))
    print (t1 - t0, 'elapsed.')
    flush ()

    if n_sig:
        
        out_dir = cy.utils.ensure_dir (
            '{}/gp_bg_ps/trials/{}/{}/{}/{}/dec/{:+08.3f}/nsig/{:08.3f}'.format (
                state.base_dir, state.ana_name,
                temp,
                'poisson' if poisson else 'nonpoisson',
                'corona_powerlaw' if gamma>0 else 'corona_flux',
                src_dec,
                n_sig))
    else:
        out_dir = cy.utils.ensure_dir ('{}/gp_bg_ps/trials/{}/{}/bg/{}/dec/{:+08.3f}/'.format (
            state.base_dir, state.ana_name, temp, 'corona_powerlaw' if gamma>0 else 'corona_flux',src_dec))

    out_file = '{}/trials_{:07d}__seed_{:010d}.npy'.format (
        out_dir, n_trials, seed)
    print ('-> {}'.format (out_file))
    np.save (out_file, trials.as_array)
if __name__ == '__main__':
    exe_t0 = now ()
    print ('start at {} .'.format (exe_t0))
    cli ()
