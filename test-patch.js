const BASE_URL = 'https://playground.4geeks.com/tracker/api/v1';

async function testPatch() {
  try {
    console.log('--- Creando un record de prueba con campos requeridos ---');
    const createRes = await fetch(`${BASE_URL}/records`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        full_name: 'Test Candidate',
        email: 'test@example.com',
        phone: '123456789',
        position: 'Developer',
        experience_years: 3,
        status: 'applied',
        step: 'application',
        stage: 'screening'
      })
    });
    
    if (!createRes.ok) {
      console.log('No se pudo crear el record:', await createRes.text());
      return;
    }
    
    const record = await createRes.json();
    const id = record.id;
    console.log(`Usando record ID: ${id}`);
    console.log('Estado inicial:', { status: record.status, step: record.step, stage: record.stage });

    const patches = [
      { status: 'reviewing' },
      { step: 'initial_screen' },
      { stage: 'personal_interview' }
    ];

    for (const patch of patches) {
      console.log(`\n--- Intentando PATCH con: ${JSON.stringify(patch)} ---`);
      const res = await fetch(`${BASE_URL}/records/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
      console.log(`Status HTTP: ${res.status}`);
      const data = await res.json();
      console.log('Respuesta JSON:', JSON.stringify(data));
      if (res.ok) {
        console.log('Campos después del PATCH:', { status: data.status, step: data.step, stage: data.stage });
      } else {
        console.log('Error en PATCH:', JSON.stringify(data));
      }
    }
  } catch (error) {
    console.error('Error:', error);
  }
}

testPatch();
