document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('applicationForm');
  const formStatus = document.getElementById('formStatus');
  const clearButton = document.getElementById('clearButton');

  if (!form || !formStatus || !clearButton) {
    return;
  }

  const fields = {
    firstName: document.getElementById('firstName'),
    lastName: document.getElementById('lastName'),
    dateOfBirth: document.getElementById('dateOfBirth'),
    nationalId: document.getElementById('nationalId'),
    email: document.getElementById('email'),
    phone: document.getElementById('phone'),
    region: document.getElementById('region'),
    postalCode: document.getElementById('postalCode'),
    preferredClinic: document.getElementById('preferredClinic'),
    dataConsent: document.getElementById('dataConsent')
  };

  const clinicRegionMap = {
    US: [
      'Houston, TX',
      'Dallas, TX',
      'Austin, TX',
      'Miami, FL',
      'Orlando, FL',
      'Tampa, FL',
      'Atlanta, GA',
      'Savannah, GA',
      'Augusta, GA'
    ],
    UK: [
      'London Central',
      'Manchester North',
      'Manchester South'
    ]
  };

  const disposableDomains = new Set([
    'mailinator.com',
    '10minutemail.com',
    'guerrillamail.com',
    'tempmail.com',
    'yopmail.com'
  ]);

  const errorFor = (fieldName) => document.getElementById(`${fieldName}Error`);

  const markNeutral = (element) => {
    if (!element) return;
    element.classList.remove('border-red-400', 'ring-2', 'ring-red-300', 'bg-red-950/20');
    element.classList.remove('border-emerald-400', 'ring-2', 'ring-emerald-300', 'bg-emerald-950/20');
    element.classList.add('border-slate-600');
    element.removeAttribute('aria-invalid');
  };

  const markError = (element, message, errorElement) => {
    if (!element || !errorElement) return;
    element.classList.remove('border-slate-600', 'border-emerald-400', 'ring-emerald-300', 'bg-emerald-950/20');
    element.classList.add('border-red-400', 'ring-2', 'ring-red-300', 'bg-red-950/20');
    element.setAttribute('aria-invalid', 'true');
    errorElement.textContent = message;
    errorElement.classList.remove('hidden');
  };

  const markSuccess = (element, errorElement) => {
    if (!element || !errorElement) return;
    element.classList.remove('border-slate-600', 'border-red-400', 'ring-red-300', 'bg-red-950/20');
    element.classList.add('border-emerald-400', 'ring-2', 'ring-emerald-300', 'bg-emerald-950/20');
    element.removeAttribute('aria-invalid');
    errorElement.textContent = '';
    errorElement.classList.add('hidden');
  };

  const normalizePhone = (value) => value.replace(/[\s()-]/g, '');

  const validateField = (name) => {
    const value = fields[name];
    const errorElement = errorFor(name);
    if (!value || !errorElement) return true;

    const raw = value.type === 'checkbox' ? value.checked : value.value.trim();
    const region = fields.region.value;

    if (name === 'firstName') {
      if (!raw) return markError(value, 'Ingresa tu nombre.', errorElement), false;
      if (raw.length < 2) return markError(value, 'El nombre debe tener al menos 2 caracteres.', errorElement), false;
      if (!/^[\p{L}\s'-]+$/u.test(raw)) return markError(value, 'El nombre solo puede contener letras, espacios, apóstrofe y guion.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'lastName') {
      if (!raw) return markError(value, 'Ingresa tus apellidos.', errorElement), false;
      if (raw.length < 2) return markError(value, 'Los apellidos deben tener al menos 2 caracteres.', errorElement), false;
      if (!/^[\p{L}\s'-]+$/u.test(raw)) return markError(value, 'Los apellidos solo pueden contener letras, espacios, apóstrofe y guion.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'dateOfBirth') {
      if (!raw) return markError(value, 'Selecciona tu fecha de nacimiento.', errorElement), false;
      const birthDate = new Date(raw);
      if (Number.isNaN(birthDate.getTime())) return markError(value, 'La fecha de nacimiento no es válida.', errorElement), false;
      const now = new Date();
      if (birthDate > now) return markError(value, 'La fecha de nacimiento no puede estar en el futuro.', errorElement), false;
      const age = now.getFullYear() - birthDate.getFullYear();
      const m = now.getMonth() - birthDate.getMonth();
      const correctedAge = m < 0 || (m === 0 && now.getDate() < birthDate.getDate()) ? age - 1 : age;
      if (correctedAge < 18) return markError(value, 'Debes tener 18 años o más para registrarte.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'nationalId') {
      if (!raw) return markError(value, 'Ingresa tu documento de identidad.', errorElement), false;
      if (!/^[A-Za-z0-9-]{6,20}$/.test(raw)) return markError(value, 'El documento debe tener entre 6 y 20 caracteres alfanuméricos (se permite guion).', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'email') {
      if (!raw) return markError(value, 'Ingresa tu correo electrónico.', errorElement), false;
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
      if (!emailRegex.test(raw)) return markError(value, 'Usa un correo válido, por ejemplo nombre@dominio.com.', errorElement), false;
      const domain = raw.split('@')[1].toLowerCase();
      if (disposableDomains.has(domain)) return markError(value, 'Usa un correo personal o corporativo permanente; no se aceptan dominios temporales.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'phone') {
      if (!raw) return markError(value, 'Ingresa tu teléfono móvil.', errorElement), false;
      if (!region) return markError(value, 'Selecciona primero la región para validar el teléfono.', errorElement), false;

      const normalized = normalizePhone(raw);
      const usPhone = /^\+?1?\d{10}$/;
      const ukPhone = /^(\+44\d{10}|0\d{10})$/;

      if (region === 'US' && !usPhone.test(normalized)) {
        return markError(value, 'Para EE.UU. usa 10 dígitos, con +1 opcional al inicio.', errorElement), false;
      }
      if (region === 'UK' && !ukPhone.test(normalized)) {
        return markError(value, 'Para Reino Unido usa +44 seguido de 10 dígitos o un número local de 11 dígitos iniciado en 0.', errorElement), false;
      }
      return markSuccess(value, errorElement), true;
    }

    if (name === 'region') {
      if (!raw) return markError(value, 'Selecciona la región de atención para aplicar la normativa correcta.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    if (name === 'postalCode') {
      if (!raw) return markError(value, 'Ingresa el código postal.', errorElement), false;
      if (!region) return markError(value, 'Selecciona primero la región para validar el código postal.', errorElement), false;

      const usZip = /^\d{5}(-\d{4})?$/;
      const ukPostcode = /^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$/i;

      if (region === 'US' && !usZip.test(raw)) {
        return markError(value, 'Para EE.UU. usa formato 12345 o 12345-6789.', errorElement), false;
      }
      if (region === 'UK' && !ukPostcode.test(raw.toUpperCase())) {
        return markError(value, 'Para Reino Unido usa formato válido, por ejemplo SW1A 1AA.', errorElement), false;
      }
      return markSuccess(value, errorElement), true;
    }

    if (name === 'preferredClinic') {
      if (!raw) return markError(value, 'Selecciona una clínica preferida.', errorElement), false;
      if (!region) return markError(value, 'Selecciona la región antes de elegir clínica.', errorElement), false;
      if (!clinicRegionMap[region].includes(raw)) {
        return markError(value, 'La clínica seleccionada no corresponde a la región elegida.', errorElement), false;
      }
      return markSuccess(value, errorElement), true;
    }

    if (name === 'dataConsent') {
      if (!raw) return markError(value, 'Debes aceptar el tratamiento de datos para continuar.', errorElement), false;
      return markSuccess(value, errorElement), true;
    }

    return true;
  };

  const managedFields = Object.keys(fields);

  const validateAll = () => {
    let firstInvalid = null;
    let errors = 0;

    for (const fieldName of managedFields) {
      const isValid = validateField(fieldName);
      if (!isValid) {
        errors += 1;
        if (!firstInvalid) firstInvalid = fields[fieldName];
      }
    }

    return { errors, firstInvalid };
  };

  const showStatus = (type, message) => {
    formStatus.classList.remove('hidden', 'border-red-400', 'bg-red-950/40', 'text-red-100', 'border-emerald-400', 'bg-emerald-950/40', 'text-emerald-100');
    if (type === 'error') {
      formStatus.classList.add('border-red-400', 'bg-red-950/40', 'text-red-100');
      formStatus.setAttribute('aria-live', 'assertive');
    } else {
      formStatus.classList.add('border-emerald-400', 'bg-emerald-950/40', 'text-emerald-100');
      formStatus.setAttribute('aria-live', 'polite');
    }
    formStatus.textContent = message;
  };

  const clearStatus = () => {
    formStatus.textContent = '';
    formStatus.classList.add('hidden');
  };

  for (const fieldName of managedFields) {
    const element = fields[fieldName];
    if (!element) continue;

    const eventType = element.type === 'checkbox' || element.tagName === 'SELECT' ? 'change' : 'input';

    element.addEventListener(eventType, () => {
      validateField(fieldName);
      clearStatus();

      if (fieldName === 'region') {
        validateField('phone');
        validateField('postalCode');
        validateField('preferredClinic');
      }
    });

    element.addEventListener('blur', () => {
      validateField(fieldName);
    });
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const { errors, firstInvalid } = validateAll();

    if (errors > 0) {
      const plural = errors === 1 ? 'error' : 'errores';
      showStatus('error', `Detectamos ${errors} ${plural}. Revisa los campos marcados para continuar.`);
      if (firstInvalid) {
        firstInvalid.focus();
      }
      return;
    }

    showStatus('success', 'Registro validado correctamente. Los datos están listos para envío seguro.');
    form.reset();
    for (const fieldName of managedFields) {
      const element = fields[fieldName];
      const errorElement = errorFor(fieldName);
      markNeutral(element);
      if (errorElement) {
        errorElement.textContent = '';
        errorElement.classList.add('hidden');
      }
    }
    fields.firstName.focus();
  });

  clearButton.addEventListener('click', () => {
    clearStatus();
    for (const fieldName of managedFields) {
      const element = fields[fieldName];
      const errorElement = errorFor(fieldName);
      markNeutral(element);
      if (errorElement) {
        errorElement.textContent = '';
        errorElement.classList.add('hidden');
      }
    }
    fields.firstName.focus();
  });
});
